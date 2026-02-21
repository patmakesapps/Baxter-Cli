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

    print(label + (wrap_line(lines[0], body_width)[0] if lines[0] else ""))
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


def print_tool_result(tool_result: dict, clip_fn) -> None:
    status = result_status(tool_result)
    color = GREEN if status == "ok" else (YELLOW if status == "failed" else RED)
    print(f"  status: {c(status, color)}")

    cmd_parts = tool_result.get("cmd")
    if isinstance(cmd_parts, list) and cmd_parts:
        print(c("  command:", DIM) + f" {' '.join(cmd_parts)}")
    if "cwd" in tool_result:
        print(f"  cwd: {tool_result['cwd']}")
    if "exit_code" in tool_result:
        print(f"  exit_code: {tool_result['exit_code']}")
    if tool_result.get("error"):
        print(f"  error: {tool_result['error']}")

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
    print(c("  /providers", GREEN))
    print(c("  /provider <groq|openai|anthropic>", GREEN))
    print(c("  /models", GREEN))
    print(c("  /model <model_name>", GREEN))
    print(c("  v          (alias for /lastdiff)", GREEN))
    print(c("  /lastdiff  (show last apply_diff unified diff)", GREEN))
    print(c("  /settings  (alias for /providers)", GREEN))
    print(c("  /help", GREEN))


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
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return buf.strip()
        if ch in ("\x00", "\xe0"):
            _ = msvcrt.getwch()
            continue
        if ch == "\x03":
            raise SystemExit(130)
        if ch == "\b":
            if buf:
                buf = buf[:-1]
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch == "/" and buf == "":
            sys.stdout.write("/\n")
            sys.stdout.flush()
            slash_picker(session)
            return None
        if ch and ch >= " ":
            buf += ch
            sys.stdout.write(ch)
            sys.stdout.flush()


def handle_ui_command(text: str, session: dict) -> bool:
    raw = text.strip()
    t = raw.lower()

    if raw and set(raw) == {"/"}:
        slash_picker(session)
        return True

    if t in {"/help", "help", "/h", "?"}:
        print_help()
        return True

    if t in {"/providers", "/settings"}:
        print_providers(session)
        return True

    if t in {"/lastdiff", "v", "view", "view +/-"}:
        last = session.get("last_diff")
        if not isinstance(last, str) or not last.strip():
            print("No diff available yet.")
            return True
        print_colored_diff(last)
        return True

    if t.startswith("/provider "):
        provider = raw.split(maxsplit=1)[1].strip().lower()
        if provider not in PROVIDERS:
            print(f"Unknown provider: {provider}")
            return True
        if not provider_has_key(provider):
            print(f"Cannot switch provider: missing {PROVIDERS[provider]['env_key']}")
            return True
        session["provider"] = provider
        session["model_override"] = None
        print(c(f"Provider set to {provider}. Model reset to {active_model(session)}", GREEN))
        return True

    if t == "/models":
        print_models(session)
        return True

    if t.startswith("/model "):
        value = raw.split(maxsplit=1)[1].strip()
        session["model_override"] = value
        print(c(f"Model set to: {active_model(session)}", GREEN))
        return True

    if t.startswith("/"):
        print("Unknown command. Use /help.")
        return True

    return False


def requires_confirmation(tool_call: dict):
    tool = tool_call.get("tool")
    args = tool_call.get("args", {}) or {}
    if tool == "delete_path":
        return True, f'Confirm delete_path for "{args.get("path", "")}"? [y/N]: '
    if tool == "apply_diff":
        return True, f'Confirm apply_diff to "{args.get("path", "")}"? [y/N] (press p to preview): '
    if tool == "write_file" and bool(args.get("overwrite", False)):
        return True, f'Confirm overwrite write_file for "{args.get("path", "")}"? [y/N]: '
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
    if tool_call and tool_call.get("tool") == "apply_diff" and IS_WINDOWS and sys.stdin.isatty():
        import msvcrt

        preview_visible = False
        preview_line_count = 0
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            ch = msvcrt.getwch().lower()
            if ch == "\x03":
                raise SystemExit(130)
            if ch in {"y"}:
                if preview_visible and preview_line_count:
                    clear_rendered_lines(preview_line_count)
                sys.stdout.write("y\n")
                sys.stdout.flush()
                return True
            if ch in {"n", "\r", "\n", "\x1b"}:
                if preview_visible and preview_line_count:
                    clear_rendered_lines(preview_line_count)
                sys.stdout.write("n\n")
                sys.stdout.flush()
                return False
            if ch == "p":
                if not preview_visible:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    preview_text = get_apply_diff_preview_text(tool_call)
                    preview_line_count = print_preview_with_count(preview_text)
                    preview_visible = True
                else:
                    clear_rendered_lines(preview_line_count)
                    preview_visible = False
                    preview_line_count = 0
                sys.stdout.write(prompt)
                sys.stdout.flush()
                continue

    while True:
        try:
            answer = input(prompt).strip().lower()
        except KeyboardInterrupt:
            raise SystemExit(130)
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        if answer == "p" and tool_call and tool_call.get("tool") == "apply_diff":
            print_colored_diff(get_apply_diff_preview_text(tool_call))
            continue
        print("Enter y, n, or p.")
