import json
import os
import re
import sys
import threading
import time
import difflib

from dotenv import load_dotenv

from baxter.providers import (
    PROVIDERS,
    call_provider,
    get_default_model,
    get_provider_models,
    provider_has_key,
)
from baxter.tools.registry import TOOL_NAMES, render_registry_for_prompt, run_tool
from baxter.tools.safe_path import resolve_in_root

load_dotenv(override=True)

BOOT_BANNER = r"""
██████╗  █████╗ ██╗  ██╗████████╗███████╗██████╗
██╔══██╗██╔══██╗╚██╗██╔╝╚══██╔══╝██╔════╝██╔══██╗
██████╔╝███████║ ╚███╔╝    ██║   █████╗  ██████╔╝
██╔══██╗██╔══██║ ██╔██╗    ██║   ██╔══╝  ██╔══██╗
██████╔╝██║  ██║██╔╝ ██╗   ██║   ███████╗██║  ██║
╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝

      ⟡ B A X T E R  •  Neural Reasoning Engine Online ⟡
──────────────────────────────────────────────────────────
  CORE: STABLE   |   TOOL MODULES: READY   |   SAFETY: ON
──────────────────────────────────────────────────────────
"""

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"
IS_WINDOWS = os.name == "nt"


def _supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.getenv("TERM", "")
    return term.lower() != "dumb"


def _c(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{RESET}"


def _cu(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{UNDERLINE}{text}{RESET}"


def build_system_prompt() -> str:
    return f"""You are Baxter, a helpful coding assistant.

You have OPTIONAL access to a tool registry. Use a tool ONLY when necessary to complete the user's request correctly.

{render_registry_for_prompt()}

TOOL CALL RULES:
- If you decide to use a tool, your entire response MUST be ONLY valid JSON on a single line:
  {{"tool":"<tool_name>","args":{{...}}}}
- tool must be one of: {", ".join(TOOL_NAMES)}
- Do not include any extra text before or after the JSON (no markdown, no explanation).
- Return exactly ONE tool call per response. Never return multiple JSON objects.
- If no tool is needed, respond normally in plain English.
- If the user asks what is inside a file / to view / open / read / show contents, you MUST call read_file.
- If the user asks to list a directory, you MUST call list_dir.
- If the user asks to create a folder/directory, you MUST call make_dir.
- If the user asks to delete/remove a file or folder, you MUST call delete_path.
- If the user asks to create a NEW file, you MUST call write_file.
- If the user asks to change/edit/modify a file, you MUST call read_file first, then apply_diff.
- Only use write_file with overwrite=true for full rewrites when apply_diff is not suitable.
- If the user asks to run a terminal command, you MUST call run_cmd (only allowed commands will work).
- If the user asks to do git actions (status/add/commit/push/pull/etc), you MUST call git_cmd.
- If the user asks to search the codebase/files for text or symbols, you MUST call search_code.
- If the user asks to "commit and push" (or equivalent), you MUST do: git add -> git commit -> git push.
- You MUST NOT call git push if there are uncommitted changes.
- Before any git push, ensure the latest git commit step succeeded (exit_code 0).
- If replying with instructions, use numbered or bullet points.
- You MUST NOT claim you created/modified/deleted anything unless a tool result says ok:true.
- Never include code blocks or include explanations when calling tools.
"""


def active_model(session: dict) -> str:
    override = session.get("model_override")
    if isinstance(override, str) and override.strip():
        return override.strip()
    return get_default_model(session.get("provider", "groq"))


def pick_startup_provider() -> str:
    for name in ("anthropic", "openai", "groq"):
        if provider_has_key(name):
            return name
    return "anthropic"


def try_parse_tool_call(text: str):
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "tool" in obj and "args" in obj:
            return obj
    except Exception:
        pass

    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        start = text.find("{", i)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
            if isinstance(obj, dict) and "tool" in obj and "args" in obj:
                return obj
            i = start + max(1, end)
        except Exception:
            i = start + 1

    # Support XML-like tool call blocks some models emit, e.g.:
    # <function_calls><invoke name="read_file"><parameter name="path">README.md</parameter></invoke></function_calls>
    invoke_match = re.search(
        r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if invoke_match:
        tool_name = invoke_match.group(1).strip()
        invoke_body = invoke_match.group(2)
        args: dict = {}
        for p in re.finditer(
            r"<parameter\s+name=\"([^\"]+)\"\s*>(.*?)</parameter>",
            invoke_body,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            key = p.group(1).strip()
            raw_val = p.group(2).strip()
            val: object = raw_val
            low = raw_val.lower()
            if low in {"true", "false"}:
                val = low == "true"
            elif re.fullmatch(r"-?\d+", raw_val):
                try:
                    val = int(raw_val)
                except Exception:
                    val = raw_val
            elif raw_val.startswith("[") or raw_val.startswith("{"):
                try:
                    val = json.loads(raw_val)
                except Exception:
                    val = raw_val
            args[key] = val
        if tool_name and isinstance(args, dict):
            return {"tool": tool_name, "args": args}
    return None


def _looks_like_broken_tool_call(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # Heuristic: model attempted tool JSON but produced malformed output.
    if '"tool"' in t and '"args"' in t:
        return True
    if t.startswith("{") and ("tool" in t or "args" in t):
        return True
    return False


def last_n_turns(messages, n_turns=6):
    system = messages[0]
    tail = messages[1:]
    trimmed_tail = tail[-(n_turns * 2) :]
    return [system] + trimmed_tail


def _clip(text: str, max_chars: int = 800) -> str:
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def print_tool_event(tool_call: dict, index: int) -> None:
    print(_c(f"[Tool {index}] ", GREEN) + f"{tool_call.get('tool', 'unknown')}")


def _result_status(tool_result: dict) -> str:
    ok = bool(tool_result.get("ok"))
    exit_code = tool_result.get("exit_code")
    if not ok:
        return "error"
    if isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    return "ok"


def print_tool_result(tool_result: dict) -> None:
    status = _result_status(tool_result)
    color = GREEN if status == "ok" else (YELLOW if status == "failed" else RED)
    print(f"  status: {_c(status, color)}")

    cmd_parts = tool_result.get("cmd")
    if isinstance(cmd_parts, list) and cmd_parts:
        print(_c("  command:", DIM) + f" {' '.join(cmd_parts)}")
    if "cwd" in tool_result:
        print(f"  cwd: {tool_result['cwd']}")
    if "exit_code" in tool_result:
        print(f"  exit_code: {tool_result['exit_code']}")
    if tool_result.get("error"):
        print(f"  error: {tool_result['error']}")

    stdout = _clip(str(tool_result.get("stdout", "")).strip())
    stderr = _clip(str(tool_result.get("stderr", "")).strip())
    if stdout:
        print("  stdout:")
        print(stdout)
    if stderr:
        print("  stderr:")
        print(stderr)
    if tool_result.get("diff_available"):
        added = int(tool_result.get("added_lines", 0))
        removed = int(tool_result.get("removed_lines", 0))
        print(
            "  diff:",
            _cu("view +/-", GREEN),
            _c(f"(+{added} -{removed})", GREEN),
            _c("type v or /lastdiff", DIM),
        )


def print_separator(label: str) -> None:
    print(_c(f"\n--- {label} ---", GREEN))


class _WorkingIndicator:
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
        # Clear spinner line before normal output resumes.
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def _run(self) -> None:
        frames = ["   ", ".  ", ".. ", "..."]
        i = 0
        while not self._stop.is_set():
            dots = frames[i % len(frames)]
            sys.stdout.write(f"\r{_c(self.label, DIM)}{dots}")
            sys.stdout.flush()
            i += 1
            time.sleep(0.2)


def _print_providers(session: dict) -> None:
    active = session.get("provider", "groq")
    print(_c("Providers:", GREEN))
    for name in PROVIDERS:
        marker = "*" if name == active else " "
        key_state = "ready" if provider_has_key(name) else "missing key"
        print(_c(f"  [{marker}] {name} ({key_state})", GREEN))
        print(_c(f"      default: {get_default_model(name)}", GREEN))


def _print_models(session: dict) -> None:
    provider = session.get("provider", "groq")
    current = active_model(session)
    print(_c(f"Models for {provider}:", GREEN))
    print(_c(f"  current: {current}", GREEN))
    print(_c("  default: provider default", GREEN))
    for model in get_provider_models(provider):
        print(_c(f"  - {model}", GREEN))


def _print_help() -> None:
    print(_c("Commands:", GREEN))
    print(_c("  /providers", GREEN))
    print(_c("  /provider <groq|openai|anthropic>", GREEN))
    print(_c("  /models", GREEN))
    print(_c("  /model <model_name>", GREEN))
    print(_c("  /model default", GREEN))
    print(_c("  v          (alias for /lastdiff)", GREEN))
    print(_c("  /lastdiff  (show last apply_diff unified diff)", GREEN))
    print(_c("  /settings  (alias for /providers)", GREEN))
    print(_c("  /help", GREEN))


def _print_colored_diff(diff_text: str) -> None:
    if not diff_text.strip():
        print("No diff content.")
        return
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            print(_c(line, GREEN))
            continue
        if line.startswith("---"):
            print(_c(line, RED))
            continue
        if line.startswith("@@"):
            print(_c(line, YELLOW))
            continue
        if line.startswith("+"):
            print(_c(line, GREEN))
            continue
        if line.startswith("-"):
            print(_c(line, RED))
            continue
        print(line)


def _render_picker_list(title: str, options: list[str], selected: int, first: bool = False) -> None:
    if first:
        print(_c(title, GREEN))
        for i, option in enumerate(options, start=1):
            marker = ">" if (i - 1) == selected else " "
            print(_c(f" {marker} {i}) {option}", GREEN))
        sys.stdout.flush()
        return

    # Repaint only the option lines in place.
    line_count = len(options)
    if line_count > 0:
        sys.stdout.write(f"\033[{line_count}F")
    for i, option in enumerate(options, start=1):
        marker = ">" if (i - 1) == selected else " "
        sys.stdout.write("\r\033[K")
        sys.stdout.write(_c(f" {marker} {i}) {option}", GREEN))
        sys.stdout.write("\n")
    sys.stdout.flush()


def _pick_with_arrows(title: str, options: list[str]) -> int | None:
    if not options:
        return None
    if not IS_WINDOWS or not sys.stdin.isatty():
        print(_c(title, GREEN))
        for i, option in enumerate(options, start=1):
            print(_c(f"  {i}) {option}", GREEN))
        raw = input("Choose number (Enter to cancel): ").strip()
        if not raw or not raw.isdigit():
            return None
        idx = int(raw) - 1
        if idx < 0 or idx >= len(options):
            return None
        return idx

    import msvcrt

    selected = 0
    print(_c("Use Up/Down and Enter. Esc cancels.", GREEN))
    _render_picker_list(title, options, selected, first=True)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            if key in {"H", "K"}:  # up/left
                selected = (selected - 1) % len(options)
                _render_picker_list(title, options, selected)
            elif key in {"P", "M"}:  # down/right
                selected = (selected + 1) % len(options)
                _render_picker_list(title, options, selected)
            continue
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return selected
        if ch == "\x1b":  # Esc
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None


def _slash_picker(session: dict) -> None:
    providers = list(PROVIDERS.keys())
    pidx = _pick_with_arrows("Choose provider:", providers)
    if pidx is None:
        return
    provider = providers[pidx]
    if not provider_has_key(provider):
        print(f"Cannot switch provider: missing {PROVIDERS[provider]['env_key']}")
        return

    session["provider"] = provider
    session["model_override"] = None

    model_options = ["provider default"] + get_provider_models(provider)
    midx = _pick_with_arrows(f"Choose model for {provider}:", model_options)
    if midx is None or midx == 0:
        session["model_override"] = None
        print(_c(f"Provider set to {provider}. Using default model: {active_model(session)}", GREEN))
        return
    session["model_override"] = model_options[midx]
    print(_c(f"Provider set to {provider}. Model set to: {active_model(session)}", GREEN))


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
            raise KeyboardInterrupt
        if ch == "\b":
            if buf:
                buf = buf[:-1]
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch == "/" and buf == "":
            sys.stdout.write("/\n")
            sys.stdout.flush()
            _slash_picker(session)
            return None
        if ch and ch >= " ":
            buf += ch
            sys.stdout.write(ch)
            sys.stdout.flush()


def handle_ui_command(text: str, session: dict) -> bool:
    raw = text.strip()
    t = raw.lower()

    if raw and set(raw) == {"/"}:
        _slash_picker(session)
        return True

    if t in {"/help", "help", "/h", "?"}:
        _print_help()
        return True

    if t in {"/providers", "/settings"}:
        _print_providers(session)
        return True

    if t in {"/lastdiff", "v", "view", "view +/-"}:
        last = session.get("last_diff")
        if not isinstance(last, str) or not last.strip():
            print("No diff available yet.")
            return True
        _print_colored_diff(last)
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
        print(_c(f"Provider set to {provider}. Model reset to {active_model(session)}", GREEN))
        return True

    if t == "/models":
        _print_models(session)
        return True

    if t.startswith("/model "):
        value = raw.split(maxsplit=1)[1].strip()
        if value.lower() == "default":
            session["model_override"] = None
            print(_c(f"Model reset to default: {active_model(session)}", GREEN))
            return True
        session["model_override"] = value
        print(_c(f"Model set to: {active_model(session)}", GREEN))
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


def _get_apply_diff_preview_text(tool_call: dict) -> str:
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


def _print_preview_with_count(diff_text: str) -> int:
    lines = diff_text.splitlines()
    if not lines:
        print("No diff changes.")
        return 1
    _print_colored_diff(diff_text)
    return len(lines)


def _clear_rendered_lines(line_count: int) -> None:
    if line_count <= 0:
        return
    if not _supports_color():
        print(_c("[preview hidden]", DIM))
        return
    # Move to the first preview line, clear each line, then return to prompt row.
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
            if ch in {"y"}:
                if preview_visible and preview_line_count:
                    _clear_rendered_lines(preview_line_count)
                sys.stdout.write("y\n")
                sys.stdout.flush()
                return True
            if ch in {"n", "\r", "\n", "\x1b"}:
                if preview_visible and preview_line_count:
                    _clear_rendered_lines(preview_line_count)
                sys.stdout.write("n\n")
                sys.stdout.flush()
                return False
            if ch == "p":
                if not preview_visible:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    preview_text = _get_apply_diff_preview_text(tool_call)
                    preview_line_count = _print_preview_with_count(preview_text)
                    preview_visible = True
                else:
                    _clear_rendered_lines(preview_line_count)
                    preview_visible = False
                    preview_line_count = 0
                sys.stdout.write(prompt)
                sys.stdout.flush()
                continue
        # unreachable

    while True:
        answer = input(prompt).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        if answer == "p" and tool_call and tool_call.get("tool") == "apply_diff":
            _preview_apply_diff(tool_call)
            continue
        print("Enter y, n, or p.")


def preflight_tool_check(tool_call: dict):
    tool = tool_call.get("tool")
    args = tool_call.get("args", {}) or {}
    if tool == "git_cmd" and str(args.get("subcommand", "")).strip().lower() == "push":
        status_result = run_tool(
            "git_cmd",
            {
                "subcommand": "status",
                "args": ["--porcelain"],
                "cwd": args.get("cwd", "."),
            },
        )
        if not status_result.get("ok"):
            return {
                "ok": False,
                "error": "pre-push check failed: unable to verify working tree status",
                "precheck": True,
            }
        if int(status_result.get("exit_code", 1)) != 0:
            return {
                "ok": False,
                "error": "pre-push check failed: git status returned non-zero exit code",
                "precheck": True,
            }
        if str(status_result.get("stdout", "")).strip():
            return {
                "ok": False,
                "error": "push blocked: uncommitted changes detected. Commit or stash changes before pushing.",
                "precheck": True,
            }
    return None


def main():
    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    os.system("cls" if os.name == "nt" else "clear")
    print(_c(BOOT_BANNER, GREEN))
    print(
        _c("Has keys:", GREEN),
        f"groq={provider_has_key('groq')} openai={provider_has_key('openai')} anthropic={provider_has_key('anthropic')}",
    )
    print("Type 'exit' to quit.\n")
    print("Use /help for provider/model commands.\n")

    session = {
        "provider": pick_startup_provider(),
        "model_override": None,
        "last_diff": None,
    }
    print(f"Active provider: {session['provider']} ({active_model(session)})\n")

    while True:
        try:
            user_text = read_user_input(session)
        except KeyboardInterrupt:
            print("\nExiting.")
            break
        if user_text is None:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break
        if handle_ui_command(user_text, session):
            continue

        messages.append({"role": "user", "content": user_text})
        tool_index = 0
        malformed_tool_retry_used = False

        while True:
            try:
                indicator = _WorkingIndicator()
                indicator.start()
                try:
                    reply = call_provider(
                        provider=session["provider"],
                        messages=last_n_turns(messages, 6),
                        model=active_model(session),
                        temperature=0.2,
                    )
                finally:
                    indicator.stop()
            except Exception as e:
                print(f"▢ {_c('Baxter:', RED)} model error: {e}")
                break

            messages.append({"role": "assistant", "content": reply})
            tool_call = try_parse_tool_call(reply)

            if not tool_call:
                if not malformed_tool_retry_used and _looks_like_broken_tool_call(reply):
                    malformed_tool_retry_used = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous response looked like a tool call but was not valid JSON. "
                                "Respond again with exactly one valid JSON object on a single line in the "
                                'form {"tool":"<tool_name>","args":{...}} and no extra text.'
                            ),
                        }
                    )
                    continue
                print(f"▢ {_c('Baxter:', GREEN)}", reply)
                break

            tool_index += 1
            print_separator(f"Tool Step {tool_index}")
            print_tool_event(tool_call, tool_index)

            precheck_result = preflight_tool_check(tool_call)
            if precheck_result is not None:
                tool_result = precheck_result
            else:
                needs_confirm, confirm_prompt = requires_confirmation(tool_call)
                if needs_confirm and not ask_confirmation(confirm_prompt, tool_call):
                    tool_result = {
                        "ok": False,
                        "error": "action canceled by user",
                        "canceled": True,
                        "tool": tool_call.get("tool"),
                    }
                else:
                    tool_result = run_tool(tool_call["tool"], tool_call["args"])
                    if (
                        tool_call.get("tool") == "apply_diff"
                        and tool_result.get("ok")
                        and isinstance(tool_result.get("diff"), str)
                    ):
                        session["last_diff"] = tool_result.get("diff")

            print_tool_result(tool_result)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "TOOL_RESULT:\n"
                        f"{json.dumps(tool_result)}\n\n"
                        "Now respond to the user in natural language with what you did. "
                        "If ok=false, explain the error and ask a single concise follow-up if needed. "
                        "Only call another tool if the user explicitly requested another action."
                    ),
                }
            )


if __name__ == "__main__":
    main()
