import difflib
import os
import re
import shutil
import sys
import textwrap
import threading
import time

from baxter.providers import PROVIDERS, get_default_model, get_provider_models, provider_has_key
from baxter.tools.safe_path import resolve_in_root

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"
IS_WINDOWS = os.name == "nt"

def supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.getenv("TERM", "")
    return term.lower() != "dumb"


def c(text: str, color: str) -> str:
    if not supports_color():
        return text
    return f"{color}{text}{RESET}"


def cu(text: str, color: str) -> str:
    if not supports_color():
        return text
    return f"{color}{UNDERLINE}{text}{RESET}"


def active_model(session: dict) -> str:
    override = session.get("model_override")
    if isinstance(override, str) and override.strip():
        return override.strip()
    return get_default_model(session.get("provider", "groq"))


def terminal_width(default: int = 100) -> int:
    try:
        return max(60, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def wrap_line(line: str, width: int, indent: str = "") -> list[str]:
    if not line:
        return [indent]
    if len(line) <= width:
        return [f"{indent}{line}"]
    wrapped = textwrap.wrap(
        line,
        width=max(20, width - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    if not wrapped:
        return [indent]
    return [f"{indent}{part}" for part in wrapped]


def strip_markdown(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    return cleaned


def print_assistant_reply(reply: str) -> None:
    label = f"▢ {c('Baxter:', GREEN)} "
    width = terminal_width()
    body_width = max(20, width - len("▢ Baxter: "))
    cleaned = strip_markdown(reply or "")
    lines = cleaned.splitlines() or [""]

    first_wrapped = wrap_line(lines[0], body_width) if lines[0] else [""]
    print(label + first_wrapped[0])
    for chunk in first_wrapped[1:]:
        print(chunk)
    for line in lines[1:]:
        wrapped = wrap_line(line, width)
        for chunk in wrapped:
            print(chunk)


def print_tool_event(tool_call: dict, index: int) -> None:
    print(c(f"[Tool {index}] ", GREEN) + f"{tool_call.get('tool', 'unknown')}")


def result_status(tool_result: dict) -> str:
    ok = bool(tool_result.get("ok"))
    exit_code = tool_result.get("exit_code")
    if not ok:
        return "error"
    if isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    return "ok"


def _cmd_words(cmd_parts) -> list[str]:
    if not isinstance(cmd_parts, list):
        return []
    words: list[str] = []
    for part in cmd_parts:
        if isinstance(part, str):
            token = part.strip().lower()
            if token:
                words.append(token)
    return words


def classify_run_cmd_step(cmd_parts) -> str | None:
    words = _cmd_words(cmd_parts)
    if not words:
        return None
    bin_name = words[0]
    args = words[1:]
    pkg_bins = {"npm", "npm.cmd", "npx", "npx.cmd", "pnpm", "yarn"}
    if bin_name not in pkg_bins:
        return None

    joined = " ".join(args)
    if "create-react-app" in joined or "create vite" in joined or "create-vite" in joined:
        return "scaffolding app"
    if any(a in {"install", "i", "ci", "add"} for a in args):
        return "installing dependencies"
    if "build" in args:
        return "building project"
    if "dev" in args or "start" in args:
        return "starting dev server"
    return "running package manager task"


def is_noisy_install_command(cmd_parts) -> bool:
    words = _cmd_words(cmd_parts)
    if not words:
        return False
    bin_name = words[0]
    args = words[1:]
    if bin_name not in {"npm", "npm.cmd", "npx", "npx.cmd", "pnpm", "yarn"}:
        return False
    if "create-react-app" in " ".join(args):
        return True
    noisy_tokens = {"install", "i", "ci", "create", "add"}
    return any(a in noisy_tokens for a in args)


def summarize_run_cmd_output(tool_result: dict) -> str | None:
    lines: list[str] = []
    for key in ("stdout", "stderr"):
        raw = str(tool_result.get(key, "")).strip()
        if raw:
            lines.extend(raw.splitlines())
    if not lines:
        return None

    patterns = (
        r"created .+ at ",
        r"added \d+ packages",
        r"up to date",
        r"audited \d+ packages",
        r"done in .+",
        r"found \d+ vulnerabilities?",
    )
    summary: list[str] = []
    for line in lines:
        compact = line.strip()
        if not compact:
            continue
        low = compact.lower()
        if any(re.search(p, low) for p in patterns):
            summary.append(compact)
    if not summary:
        return None
    deduped: list[str] = []
    for item in summary:
        if item not in deduped:
            deduped.append(item)
    return "; ".join(deduped[:3])


def print_tool_result(tool_result: dict, clip_fn) -> None:
    status = result_status(tool_result)
    color = GREEN if status == "ok" else (YELLOW if status == "failed" else RED)
    print(f"  status: {c(status, color)}")

    cmd_parts = tool_result.get("cmd")
    if isinstance(cmd_parts, list) and cmd_parts:
        print(c("  command:", DIM) + f" {' '.join(cmd_parts)}")
        stage = classify_run_cmd_step(cmd_parts)
        if stage:
            print(c(f"  step: {stage}", DIM))
    if "cwd" in tool_result:
        print(f"  cwd: {tool_result['cwd']}")
    if "exit_code" in tool_result:
        print(f"  exit_code: {tool_result['exit_code']}")
    if "success" in tool_result:
        print(f"  success: {bool(tool_result.get('success'))}")
    if tool_result.get("timed_out"):
        tsec = tool_result.get("timeout_sec")
        if isinstance(tsec, int):
            print(c(f"  result: timed out after {tsec}s", RED))
        else:
            print(c("  result: timed out", RED))
    if tool_result.get("detached"):
        pid = tool_result.get("pid")
        if isinstance(pid, int):
            print(c(f"  process: running in background (pid {pid})", GREEN))
        else:
            print(c("  process: running in background", GREEN))
    elif "pid" in tool_result:
        print(f"  pid: {tool_result['pid']}")
    if "stopped" in tool_result:
        stopped = bool(tool_result.get("stopped"))
        status_text = "stopped" if stopped else "still running"
        color = GREEN if stopped else YELLOW
        print(c(f"  process: {status_text}", color))
    if tool_result.get("message"):
        print(f"  message: {tool_result['message']}")
    if status == "failed":
        print(c("  result: command failed (non-zero exit code)", YELLOW))
    if tool_result.get("error"):
        print(f"  error: {tool_result['error']}")

    show_compact_success = status == "ok" and is_noisy_install_command(cmd_parts)
    if show_compact_success:
        summary = summarize_run_cmd_output(tool_result)
        if summary:
            print(c(f"  summary: {summary}", DIM))
        print(c("  output: suppressed noisy install logs", DIM))
        stdout = ""
        stderr = ""
    else:
        stdout = clip_fn(str(tool_result.get("stdout", "")).strip())
        stderr = clip_fn(str(tool_result.get("stderr", "")).strip())
    width = terminal_width()
    if stdout:
        print("  stdout:")
        for line in stdout.splitlines():
            for chunk in wrap_line(line, width, "    "):
                print(chunk)
    if stderr:
        print("  stderr:")
        for line in stderr.splitlines():
            for chunk in wrap_line(line, width, "    "):
                print(chunk)
    if tool_result.get("diff_available"):
        added = int(tool_result.get("added_lines", 0))
        removed = int(tool_result.get("removed_lines", 0))
        print(
            "  diff:",
            cu("view +/-", GREEN),
            c(f"(+{added} -{removed})", GREEN),
            c("type v or /lastdiff", DIM),
        )


def print_separator(label: str) -> None:
    width = terminal_width()
    text = f" {label} "
    rail = max(4, width - len(text))
    left = rail // 2
    right = rail - left
    print(c("\n" + ("-" * left) + text + ("-" * right), GREEN))


class WorkingIndicator:
    def __init__(self, label: str = "▢ Baxter is working") -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        if not sys.stdout.isatty():
            print(c(f"{self.label}...", DIM))
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _run(self) -> None:
        frames = ["   ", ".  ", ".. ", "..."]
        i = 0
        while not self._stop.is_set():
            dots = frames[i % len(frames)]
            sys.stdout.write(f"\r{c(self.label, DIM)}{dots}")
            sys.stdout.flush()
            i += 1
            time.sleep(0.2)


class CommandIndicator:
    def __init__(
        self,
        cmd_parts,
        timeout_sec: int | None = None,
        label: str = "▢ Baxter is working",
        active_step: str | None = None,
        inline: bool = True,
    ) -> None:
        self.cmd_parts = cmd_parts if isinstance(cmd_parts, list) else []
        self.timeout_sec = timeout_sec if isinstance(timeout_sec, int) and timeout_sec > 0 else None
        self.label = label
        self.active_step = active_step.strip() if isinstance(active_step, str) else None
        self.inline = bool(inline)
        self._stop = threading.Event()
        self._thread = None
        self._start_ts = 0.0

    def _cmd_text(self) -> str:
        raw = " ".join(str(x) for x in self.cmd_parts if isinstance(x, str)).strip()
        if not raw:
            raw = "(command)"
        if len(raw) > 70:
            return raw[:67] + "..."
        return raw

    def start(self) -> None:
        self._start_ts = time.time()
        if not sys.stdout.isatty():
            timeout_text = f", timeout={self.timeout_sec}s" if self.timeout_sec else ""
            print(c(f"{self.label}: {self._cmd_text()} (in progress{timeout_text})", DIM))
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        if self.inline and sys.stdout.isatty():
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def _run(self) -> None:
        frames = ["|", "/", "-", "\\"]
        i = 0
        status_text = self.active_step or self._cmd_text()
        last_logged_elapsed = -1
        while not self._stop.is_set():
            elapsed = int(max(0, time.time() - self._start_ts))
            if self.timeout_sec:
                if elapsed <= self.timeout_sec:
                    tail = f" [{elapsed}s/{self.timeout_sec}s]"
                else:
                    over = elapsed - self.timeout_sec
                    tail = f" [{elapsed}s/{self.timeout_sec}s +{over}s over]"
            else:
                tail = f" [{elapsed}s]"
            frame = frames[i % len(frames)]
            if self.inline and sys.stdout.isatty():
                sys.stdout.write(f"\r{c(self.label, DIM)} {frame} {status_text}{tail}")
                sys.stdout.flush()
            else:
                # Keep foreground heartbeat sparse so live stdout/stderr stays readable.
                if elapsed == 0 or elapsed - last_logged_elapsed >= 15:
                    print(c(f"  {self.label} {frame} {status_text}{tail}", DIM))
                    last_logged_elapsed = elapsed
            i += 1
            time.sleep(1.0 if not self.inline else 0.12)


def print_providers(session: dict) -> None:
    active = session.get("provider", "groq")
    print(c("Providers:", GREEN))
    for name in PROVIDERS:
        marker = "*" if name == active else " "
        key_state = "ready" if provider_has_key(name) else "missing key"
        print(c(f"  [{marker}] {name} ({key_state})", GREEN))
        print(c(f"      {get_default_model(name)}", GREEN))


def print_models(session: dict) -> None:
    provider = session.get("provider", "groq")
    current = active_model(session)
    print(c(f"Models for {provider}:", GREEN))
    print(c(f"  current: {current}", GREEN))
    for model in get_provider_models(provider):
        print(c(f"  - {model}", GREEN))


def print_help() -> None:
    print(c("Commands:", GREEN))
    print(c("  /models    (open provider/model picker)", GREEN))
    print(c("  /apikeys   (add/update/clear API keys)", GREEN))


def print_colored_diff(diff_text: str) -> None:
    if not diff_text.strip():
        print("No diff content.")
        return
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            print(c(line, GREEN))
            continue
        if line.startswith("---"):
            print(c(line, RED))
            continue
        if line.startswith("@@"):
            print(c(line, YELLOW))
            continue
        if line.startswith("+"):
            print(c(line, GREEN))
            continue
        if line.startswith("-"):
            print(c(line, RED))
            continue
        print(line)


def render_picker_list(title: str, options: list[str], selected: int, first: bool = False) -> None:
    if first:
        print(c(title, GREEN))
        for i, option in enumerate(options, start=1):
            marker = ">" if (i - 1) == selected else " "
            print(c(f" {marker} {i}) {option}", GREEN))
        sys.stdout.flush()
        return

    line_count = len(options)
    if line_count > 0:
        sys.stdout.write(f"\033[{line_count}F")
    for i, option in enumerate(options, start=1):
        marker = ">" if (i - 1) == selected else " "
        sys.stdout.write("\r\033[K")
        sys.stdout.write(c(f" {marker} {i}) {option}", GREEN))
        sys.stdout.write("\n")
    sys.stdout.flush()


def pick_with_arrows(title: str, options: list[str]) -> int | None:
    if not options:
        return None
    if not IS_WINDOWS or not sys.stdin.isatty():
        print(c(title, GREEN))
        for i, option in enumerate(options, start=1):
            print(c(f"  {i}) {option}", GREEN))
        raw = input("Choose number (Enter to cancel): ").strip()
        if not raw or not raw.isdigit():
            return None
        idx = int(raw) - 1
        if idx < 0 or idx >= len(options):
            return None
        return idx

    import msvcrt

    selected = 0
    print(c("Use Up/Down and Enter. Esc cancels.", GREEN))
    render_picker_list(title, options, selected, first=True)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            if key in {"H", "K"}:
                selected = (selected - 1) % len(options)
                render_picker_list(title, options, selected)
            elif key in {"P", "M"}:
                selected = (selected + 1) % len(options)
                render_picker_list(title, options, selected)
            continue
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return selected
        if ch == "\x1b":
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None


def slash_picker(session: dict) -> None:
    providers = list(PROVIDERS.keys())
    pidx = pick_with_arrows("Choose provider:", providers)
    if pidx is None:
        return
    provider = providers[pidx]
    if not provider_has_key(provider):
        print(f"Cannot switch provider: missing {PROVIDERS[provider]['env_key']}")
        return

    provider_models = get_provider_models(provider)
    model_options = list(provider_models)

    midx = pick_with_arrows(f"Choose model for {provider}:", model_options)
    if midx is None:
        return
    session["provider"] = provider
    session["model_override"] = provider_models[midx]
    print(c(f"Provider set to {provider}. Model set to: {active_model(session)}", GREEN))


def read_user_input(session: dict) -> str | None:
    if not IS_WINDOWS or not sys.stdin.isatty():
        return input("▣ You:").strip()

    import msvcrt

    sys.stdout.write("▣ You:")
    sys.stdout.flush()
    buf = ""
    source_flags: list[bool] = []
    visible_chars: list[str] = []
    pasted_char_count = 0
    last_paste_ts = 0.0
    paste_notice_shown = False

    def _is_probable_paste_chunk(chunk: list[str]) -> bool:
        if len(chunk) < 8:
            return False
        for cch in chunk:
            if cch in ("\x00", "\xe0", "\x03", "\b"):
                return False
            if cch not in ("\r", "\n", "\t") and cch < " ":
                return False
        return True

    while True:
        first = msvcrt.getwch()
        chunk = [first]
        while msvcrt.kbhit():
            chunk.append(msvcrt.getwch())

        is_paste_chunk = _is_probable_paste_chunk(chunk)
        if is_paste_chunk:
            last_paste_ts = time.time()

        for idx, ch in enumerate(chunk):
            if ch in ("\r", "\n"):
                # If input is still queued (or we just detected a paste burst),
                # treat Enter as a literal newline instead of submitting.
                is_last_in_chunk = idx == (len(chunk) - 1)
                if msvcrt.kbhit():
                    buf += "\n"
                    source_flags.append(True)
                    pasted_char_count += 1
                    continue
                if is_paste_chunk and is_last_in_chunk:
                    # Paste commonly ends with a trailing newline. Keep input
                    # pending and let user press Enter explicitly to submit.
                    continue
                if (time.time() - last_paste_ts) <= 0.06:
                    buf += "\n"
                    source_flags.append(True)
                    pasted_char_count += 1
                    continue
                sys.stdout.write("\n")
                sys.stdout.flush()
                if pasted_char_count > 0 and (not paste_notice_shown):
                    print(c(f"[{pasted_char_count} chars pasted]", GREEN))
                return buf.strip()
            if ch in ("\x00", "\xe0"):
                _ = msvcrt.getwch()
                continue
            if ch == "\x03":
                raise SystemExit(130)
            if ch == "\b":
                if buf and source_flags:
                    was_pasted = source_flags.pop()
                    removed = buf[-1]
                    buf = buf[:-1]
                    if (not was_pasted) and removed != "\n":
                        if visible_chars:
                            visible_chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                continue
            if ch and ch >= " ":
                buf += ch
                if is_paste_chunk:
                    source_flags.append(True)
                    pasted_char_count += 1
                else:
                    source_flags.append(False)
                    visible_chars.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
        if is_paste_chunk and pasted_char_count > 0:
            paste_notice_shown = True
            sys.stdout.write("\n")
            sys.stdout.flush()
            print(c(f"[{pasted_char_count} chars pasted]", GREEN))
            sys.stdout.write("▣ You:")
            if visible_chars:
                sys.stdout.write("".join(visible_chars))
            sys.stdout.flush()


def handle_ui_command(text: str, session: dict) -> bool:
    raw = text.strip()
    t = raw.lower()

    if t == "/models":
        slash_picker(session)
        return True

    if t.startswith("/"):
        print("Unknown command. Use /models or /apikeys.")
        return True

    return False


def requires_confirmation(tool_call: dict):
    tool = tool_call.get("tool")
    args = tool_call.get("args", {}) or {}
    if tool == "run_cmd":
        cmd = args.get("cmd")
        if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd):
            parts = [p.strip().lower() for p in cmd if isinstance(p, str)]
            if parts:
                bin_name = parts[0]
                starts_process = bool(args.get("detach", False)) or (
                    bin_name in {"npm", "npm.cmd", "npx", "npx.cmd", "yarn", "pnpm"}
                    and (("run" in parts and "dev" in parts) or ("start" in parts) or ("dev" in parts))
                )
                if starts_process:
                    cmd_text = " ".join(cmd).strip()
                    if len(cmd_text) > 80:
                        cmd_text = cmd_text[:77] + "..."
                    return True, f'Start process "{cmd_text}" now? [y/N]: '
    if tool == "delete_path":
        return True, f'Confirm delete_path for "{args.get("path", "")}"? [y/N]: '
    if tool == "apply_diff":
        return True, f'Confirm apply_diff to "{args.get("path", "")}"? [y/N] (press p to preview): '
    if tool == "write_file" and bool(args.get("overwrite", False)):
        return (
            True,
            f'Confirm overwrite write_file for "{args.get("path", "")}"? [y/N] (press p to preview): ',
        )
    if tool == "git_cmd":
        sub = str(args.get("subcommand", "")).strip().lower()
        if sub == "push":
            return True, "Confirm git push? [y/N]: "
        if sub == "rm":
            return True, "Confirm git rm (delete tracked files)? [y/N]: "
    return False, ""


def get_apply_diff_preview_text(tool_call: dict) -> str:
    args = tool_call.get("args", {}) or {}
    path = args.get("path")
    find = args.get("find")
    replace = args.get("replace", "")
    replace_all = bool(args.get("replace_all", False))

    if not isinstance(path, str) or not isinstance(find, str) or find == "":
        return "Cannot preview diff: missing path/find."
    if not isinstance(replace, str):
        return "Cannot preview diff: replace must be a string."

    try:
        full_path = resolve_in_root(path)
        with open(full_path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as e:
        return f"Cannot preview diff: {e}"

    hits = original.count(find)
    if hits == 0:
        return f'Cannot preview diff: find text not found in "{path}".'
    notes = []
    if not replace_all and hits > 1:
        notes.append(
            f'Preview note: find text matches {hits} locations in "{path}". '
            "Only first match will be replaced."
        )

    if replace_all:
        updated = original.replace(find, replace)
    else:
        updated = original.replace(find, replace, 1)

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not diff_lines:
        return "No diff changes."
    parts = []
    if notes:
        parts.extend(notes)
    parts.append("\n".join(diff_lines))
    return "\n".join(parts)


def get_write_file_overwrite_preview_text(tool_call: dict) -> str:
    args = tool_call.get("args", {}) or {}
    path = args.get("path")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))

    if not overwrite:
        return "Cannot preview overwrite diff: overwrite=true was not set."
    if not isinstance(path, str) or path.strip() == "":
        return "Cannot preview overwrite diff: missing/invalid path."
    if not isinstance(content, str):
        return "Cannot preview overwrite diff: content must be a string."

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return f"Cannot preview overwrite diff: {e}"

    if not os.path.exists(full_path):
        return (
            f'Cannot preview overwrite diff: "{path}" does not exist yet. '
            "This write will create a new file."
        )

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as e:
        return f"Cannot preview overwrite diff: {e}"

    updated = content
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not diff_lines:
        return "No diff changes."
    return "\n".join(diff_lines)


def print_preview_with_count(diff_text: str) -> int:
    lines = diff_text.splitlines()
    if not lines:
        print("No diff changes.")
        return 1
    print_colored_diff(diff_text)
    return len(lines)


def clear_rendered_lines(line_count: int) -> None:
    if line_count <= 0:
        return
    if not supports_color():
        print(c("[preview hidden]", DIM))
        return
    sys.stdout.write(f"\033[{line_count}F")
    for _ in range(line_count):
        sys.stdout.write("\r\033[K\n")
    if line_count > 0:
        sys.stdout.write(f"\033[{line_count}F")
    sys.stdout.flush()


def ask_confirmation(prompt: str, tool_call: dict | None = None) -> bool:
    def preview_text_for_tool(tc: dict) -> str:
        tool = tc.get("tool")
        if tool == "apply_diff":
            return get_apply_diff_preview_text(tc)
        if tool == "write_file" and bool((tc.get("args") or {}).get("overwrite", False)):
            return get_write_file_overwrite_preview_text(tc)
        return "Preview is not available for this tool."

    can_preview = bool(
        tool_call
        and (
            tool_call.get("tool") == "apply_diff"
            or (
                tool_call.get("tool") == "write_file"
                and bool((tool_call.get("args") or {}).get("overwrite", False))
            )
        )
    )

    while True:
        try:
            answer = input(prompt).strip().lower()
        except KeyboardInterrupt:
            raise SystemExit(130)
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        if answer == "p" and can_preview:
            print_colored_diff(preview_text_for_tool(tool_call))
            continue
        print("Enter y, n, or p.")
