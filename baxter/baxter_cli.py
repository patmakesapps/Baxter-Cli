import os
import json
import sys
import urllib.request
import urllib.error
from dotenv import load_dotenv

from baxter.tools.registry import render_registry_for_prompt, run_tool, TOOL_NAMES

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

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
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.getenv("TERM", "")
    if term.lower() == "dumb":
        return False
    return True


def _c(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{RESET}"


def build_system_prompt() -> str:
    return f"""You are Baxter, a helpful coding assistant.

You have OPTIONAL access to a tool registry. Use a tool ONLY when necessary to complete the user’s request correctly.

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
- If the user asks to delete/remove a file or folder, you MUST call delete_path (note: it only deletes empty folders).
- If the user asks to create a NEW file, you MUST call write_file.
- If the user asks to change/edit/modify a file, you MUST call read_file first, then write_file.
- If the user asks to run a terminal command, you MUST call run_cmd (only allowed commands will work).
- If the user asks to do git actions (status/add/commit/push/pull/etc), you MUST call git_cmd.
- If the user asks to "commit and push" (or equivalent), you MUST do: git add -> git commit -> git push.
- You MUST NOT call git push if there are uncommitted changes.
- Before any git push, ensure the latest git commit step succeeded (exit_code 0).
- If replying with instructions, use numbered or bullet points.
- You MUST NOT claim you created/modified/deleted anything unless a tool result says ok:true.
- Never include code blocks or include explanations when calling tools.
"""


def call_model(messages, model="llama-3.1-8b-instant", temperature=0.2) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing. Put it in .env and restart.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Needed on some networks due to Cloudflare/WAF behavior:
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def try_parse_tool_call(text: str):
    # 1) strict: whole response is JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "tool" in obj and "args" in obj:
            return obj
    except Exception:
        pass

    # 2) fallback: find the first valid JSON object anywhere in text
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

    return None


def last_n_turns(messages, n_turns=6):
    # messages[0] is the system prompt
    system = messages[0]
    tail = messages[1:]

    # 1 turn = user + assistant, so n_turns => 2*n_turns messages
    trimmed_tail = tail[-(n_turns * 2) :]
    return [system] + trimmed_tail


def _clip(text: str, max_chars: int = 800) -> str:
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def print_tool_event(tool_call: dict, index: int) -> None:
    tool_name = tool_call.get("tool", "unknown")
    print(_c(f"[Tool {index}] ", GREEN) + f"{tool_name}")


def _result_status(tool_result: dict) -> str:
    ok = bool(tool_result.get("ok"))
    exit_code = tool_result.get("exit_code")
    if not ok:
        return "error"
    if isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    return "ok"


def print_tool_result(tool_result: dict, show_details: bool = False) -> None:
    status = _result_status(tool_result)
    if status == "ok":
        status_text = _c(status, GREEN)
    elif status == "failed":
        status_text = _c(status, YELLOW)
    else:
        status_text = _c(status, RED)
    print(f"  status: {status_text}")

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
    has_output = bool(stdout or stderr)
    if show_details:
        if stdout:
            print("  stdout:")
            print(stdout)
        if stderr:
            print("  stderr:")
            print(stderr)
    elif has_output:
        print("  output: hidden (use /expand or /details on)")


def print_separator(label: str) -> None:
    print(_c(f"\n--- {label} ---", GREEN))


def handle_ui_command(text: str, ui: dict) -> bool:
    t = text.strip().lower()
    if t in {"/help", "help", "/h", "?"}:
        print(
            "UI commands: /details on | /details off | /details toggle | /compact | /expand | /help"
        )
        return True
    if t == "/compact":
        ui["show_details"] = False
        print("Display mode: compact")
        return True
    if t == "/expand":
        last = ui.get("last_tool_result")
        if not last:
            print("No tool result to expand yet.")
        else:
            print_separator("Last Tool Result (Expanded)")
            print_tool_result(last, show_details=True)
        return True
    if t in {"/details on", "/details off", "/details toggle"}:
        if t == "/details on":
            ui["show_details"] = True
        elif t == "/details off":
            ui["show_details"] = False
        else:
            ui["show_details"] = not ui["show_details"]
        print(f"Display details: {'on' if ui['show_details'] else 'off'}")
        return True
    return False


def requires_confirmation(tool_call: dict):
    tool = tool_call.get("tool")
    args = tool_call.get("args", {}) or {}

    if tool == "delete_path":
        path = args.get("path", "")
        return True, f'Confirm delete_path for "{path}"? [y/N]: '

    if tool == "git_cmd":
        sub = str(args.get("subcommand", "")).strip().lower()
        if sub == "push":
            return True, "Confirm git push? [y/N]: "
        if sub == "rm":
            return True, "Confirm git rm (delete tracked files)? [y/N]: "

    return False, ""


def ask_confirmation(prompt: str) -> bool:
    ans = input(prompt).strip().lower()
    return ans in {"y", "yes"}


def preflight_tool_check(tool_call: dict):
    tool = tool_call.get("tool")
    args = tool_call.get("args", {}) or {}

    # Prevent no-op/misleading pushes when there are local unstaged/staged changes.
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
    print(_c("Has GROQ_API_KEY:", GREEN), bool(os.getenv("GROQ_API_KEY")))
    print("Type 'exit' to quit.\n")
    print("Display: compact (use /help for UI commands)\n")

    ui = {"show_details": False, "last_tool_result": None}

    while True:
        user_text = input("▣ You:").strip()
        if user_text.lower() in {"exit", "quit"}:
            break
        if handle_ui_command(user_text, ui):
            continue

        messages.append({"role": "user", "content": user_text})
        tool_index = 0

        # Tool-chaining loop:
        # model -> (optional tool) -> model -> (optional tool) -> ... -> final text
        while True:
            reply = call_model(last_n_turns(messages, 6))
            messages.append({"role": "assistant", "content": reply})

            tool_call = try_parse_tool_call(reply)

            # No tool call => done for this user input
            if not tool_call:
                print(f"▢ {_c('Baxter:', GREEN)}", reply)
                break

            # Tool call => run it and feed result back
            tool_index += 1
            print_separator(f"Tool Step {tool_index}")
            print_tool_event(tool_call, tool_index)
            precheck_result = preflight_tool_check(tool_call)
            if precheck_result is not None:
                tool_result = precheck_result
            else:
                needs_confirm, confirm_prompt = requires_confirmation(tool_call)
                if needs_confirm and not ask_confirmation(confirm_prompt):
                    tool_result = {
                        "ok": False,
                        "error": "action canceled by user",
                        "canceled": True,
                        "tool": tool_call.get("tool"),
                    }
                else:
                    tool_result = run_tool(tool_call["tool"], tool_call["args"])
            ui["last_tool_result"] = tool_result
            print_tool_result(tool_result, show_details=ui["show_details"])
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
