import json
import os
import re
import sys

from dotenv import load_dotenv

from baxter import terminal_ui as tui
from baxter.providers import (
    PROVIDERS,
    call_provider,
    provider_has_key,
)
from baxter.tools.registry import TOOL_NAMES, render_registry_for_prompt, run_tool


def load_baxter_env() -> None:
    # 1) Load machine-level Baxter config for one-time key setup.
    home = os.path.expanduser("~")
    if home and home != "~":
        user_env = os.path.join(home, ".baxter", ".env")
        if os.path.isfile(user_env):
            load_dotenv(dotenv_path=user_env, override=False)

    # 2) Load per-project overrides from cwd.
    load_dotenv(override=True)


load_baxter_env()

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

MUTATING_TOOLS = {"apply_diff", "write_file", "make_dir", "delete_path", "run_cmd"}
READ_ONLY_REQUEST_HINTS = (
    "what does",
    "what is in",
    "show me",
    "read ",
    "display ",
    "contents",
    "inside",
    "cat ",
    "view ",
)
MUTATING_REQUEST_HINTS = (
    "edit",
    "change",
    "modify",
    "update",
    "fix",
    "rewrite",
    "refactor",
    "create",
    "add",
    "delete",
    "remove",
    "rename",
    "move",
    "commit",
    "push",
    "run ",
    "execute",
)


def _user_env_path() -> str | None:
    home = os.path.expanduser("~")
    if not home or home == "~":
        return None
    return os.path.join(home, ".baxter", ".env")


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                if key:
                    values[key] = v.strip()
    except Exception:
        return values
    return values


def _write_env_file(path: str, values: dict[str, str]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    lines = [f"{k}={v}" for k, v in values.items() if isinstance(k, str) and isinstance(v, str)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def maybe_prompt_api_key_setup() -> None:
    if provider_has_key("anthropic") or provider_has_key("openai") or provider_has_key("groq"):
        return
    env_path = _user_env_path()
    if not env_path:
        return

    print(
        tui.c(
            "No provider API keys found. Configure now for one-time setup in ~/.baxter/.env.",
            tui.YELLOW,
        )
    )
    answer = input("Set up API keys now? [Y/n]: ").strip().lower()
    if answer in {"n", "no"}:
        return

    existing = _parse_env_file(env_path)
    wrote_any = False
    for provider in ("openai", "anthropic", "groq"):
        env_key = str(PROVIDERS[provider]["env_key"])
        current = os.getenv(env_key, "").strip()
        prompt = f"Enter {env_key}"
        if current:
            prompt += " (press Enter to keep current)"
        prompt += ", or leave blank to skip: "
        raw = input(prompt).strip()
        if raw:
            existing[env_key] = raw
            os.environ[env_key] = raw
            wrote_any = True
        elif current:
            existing[env_key] = current
            wrote_any = True

    if not wrote_any:
        print(tui.c("No keys were entered. You can configure later in ~/.baxter/.env.", tui.YELLOW))
        return

    try:
        _write_env_file(env_path, existing)
        load_dotenv(dotenv_path=env_path, override=True)
        print(tui.c(f"Saved key config to {env_path}", tui.GREEN))
    except Exception as e:
        print(tui.c(f"Could not write key config: {e}", tui.RED))


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
- If a file path is unknown (example: user only says "edit index.html"), call search_code first with the filename to locate the correct relative path.
- For search_code, use short search terms (filename, symbol, or key phrase), not the user's entire sentence.
- Only use write_file with overwrite=true for full rewrites when apply_diff is not suitable.
- If the user asks to run a terminal command, you MUST call run_cmd (only allowed commands will work).
- If the user asks to do git actions (status/add/commit/push/pull/etc), you MUST call git_cmd.
- If the user asks to search the codebase/files for text or symbols, you MUST call search_code.
- If the user asks to "commit and push" (or equivalent), you MUST do: git add -> git commit -> git push.
- If the user asks you to commit changes, you MUST run git add and git commit yourself via git_cmd; do not ask the user to run commands.
- If a commit message is not provided, use a concise default commit message that matches the change.
- You MUST NOT call git push if there are uncommitted changes.
- Before any git push, ensure the latest git commit step succeeded (exit_code 0).
- If replying with instructions, use numbered or bullet points.
- You MUST NOT claim you created/modified/deleted anything unless a tool result says ok:true.
- read_file, list_dir, and search_code do not modify files; never claim code was changed after those tools.
- When the user asks for an edit/fix, keep calling tools until the edit is actually applied (or you are blocked).
- Never include code blocks or include explanations when calling tools.
"""


def pick_startup_provider() -> str:
    for name in ("anthropic", "openai", "groq"):
        if provider_has_key(name):
            return name
    print(
        tui.c(
            "WARNING: No provider API keys found. "
            "Set one of ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY in "
            "~/.baxter/.env (or .env in the current folder).",
            tui.YELLOW,
        )
    )
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


def looks_like_broken_tool_call(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
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


def clip(text: str, max_chars: int = 800) -> str:
    if text is None:
        return ""
    raw_limit = os.getenv("BAXTER_CLIP_CHARS", "").strip()
    if raw_limit:
        try:
            max_chars = int(raw_limit)
        except ValueError:
            max_chars = 0
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


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


def _git_is_mutating(tool_call: dict) -> bool:
    if tool_call.get("tool") != "git_cmd":
        return False
    sub = str((tool_call.get("args") or {}).get("subcommand", "")).strip().lower()
    return sub in {"add", "commit", "push", "pull", "switch", "checkout", "restore", "rm", "mv", "stash"}


def user_allows_mutations(user_text: str) -> bool:
    t = (user_text or "").strip().lower()
    if not t:
        return False
    if any(h in t for h in MUTATING_REQUEST_HINTS):
        return True
    if any(h in t for h in READ_ONLY_REQUEST_HINTS):
        return False
    if t.endswith("?"):
        return False
    return False


def tool_is_mutating(tool_call: dict) -> bool:
    tool = tool_call.get("tool")
    if tool in MUTATING_TOOLS:
        if tool == "write_file":
            return bool((tool_call.get("args") or {}).get("overwrite", False)) or True
        return True
    return _git_is_mutating(tool_call)


def should_enforce_readonly_guard(session: dict) -> bool:
    provider = str(session.get("provider", "")).strip().lower()
    model = str(tui.active_model(session)).strip().lower()
    return provider == "groq" and "llama" in model


def main():
    maybe_prompt_api_key_setup()
    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    os.system("cls" if os.name == "nt" else "clear")
    print(tui.c(BOOT_BANNER, tui.GREEN))
    print(
        tui.c("Has keys:", tui.GREEN),
        f"groq={provider_has_key('groq')} openai={provider_has_key('openai')} anthropic={provider_has_key('anthropic')}",
    )
    print("Type 'exit' to quit.\n")
    print("Use /help for provider/model commands.\n")

    session = {
        "provider": pick_startup_provider(),
        "model_override": None,
        "last_diff": None,
    }
    print(f"Active provider: {session['provider']} ({tui.active_model(session)})\n")

    while True:
        try:
            user_text = tui.read_user_input(session)
        except KeyboardInterrupt:
            raise SystemExit(130)
        if user_text is None:
            continue
        if not user_text.strip():
            continue
        if user_text.lower() in {"exit", "quit"}:
            break
        if tui.handle_ui_command(user_text, session):
            continue

        allow_mutations = user_allows_mutations(user_text)
        enforce_readonly_guard = should_enforce_readonly_guard(session)
        messages.append({"role": "user", "content": user_text})
        tool_index = 0
        malformed_tool_retry_used = False

        while True:
            try:
                indicator = tui.WorkingIndicator()
                indicator.start()
                try:
                    reply = call_provider(
                        provider=session["provider"],
                        messages=last_n_turns(messages, 6),
                        model=tui.active_model(session),
                        temperature=0.2,
                    )
                finally:
                    indicator.stop()
            except Exception as e:
                print(f"▢ {tui.c('Baxter:', tui.RED)} model error: {e}")
                break

            messages.append({"role": "assistant", "content": reply})
            tool_call = try_parse_tool_call(reply)

            if not tool_call:
                if not malformed_tool_retry_used and looks_like_broken_tool_call(reply):
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
                tui.print_assistant_reply(reply)
                break

            tool_index += 1
            tui.print_separator(f"Tool Step {tool_index}")
            tui.print_tool_event(tool_call, tool_index)

            if enforce_readonly_guard and (not allow_mutations) and tool_is_mutating(tool_call):
                tool_result = {
                    "ok": False,
                    "error": "mutating tool blocked for read-only request",
                    "blocked": True,
                    "tool": tool_call.get("tool"),
                }
                tui.print_tool_result(tool_result, clip)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "TOOL_RESULT:\n"
                            f"{json.dumps(tool_result)}\n\n"
                            "The user's current request is read-only. "
                            "Do not call mutating tools. "
                            "Use read-only tools or answer directly."
                        ),
                    }
                )
                continue

            precheck_result = preflight_tool_check(tool_call)
            if precheck_result is not None:
                tool_result = precheck_result
            else:
                needs_confirm, confirm_prompt = tui.requires_confirmation(tool_call)
                if needs_confirm and not tui.ask_confirmation(confirm_prompt, tool_call):
                    tool_result = {
                        "ok": False,
                        "error": "tool execution cancelled by user confirmation",
                        "cancelled": True,
                        "tool": tool_call.get("tool"),
                    }
                    tui.print_tool_result(tool_result, clip)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "TOOL_RESULT:\n"
                                f"{json.dumps(tool_result)}\n\n"
                                "The user denied this tool execution. "
                                "Do not retry the same mutating tool unless the user explicitly requests it. "
                                "Continue with safe read-only tools or provide a plain-English response."
                            ),
                        }
                    )
                    print(tui.c("Tell Baxter what to do differently", tui.GREEN))
                    break
                else:
                    tool_result = run_tool(tool_call["tool"], tool_call["args"])
                    if (
                        tool_call.get("tool") == "apply_diff"
                        and tool_result.get("ok")
                        and isinstance(tool_result.get("diff"), str)
                    ):
                        session["last_diff"] = tool_result.get("diff")

            tui.print_tool_result(tool_result, clip)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "TOOL_RESULT:\n"
                        f"{json.dumps(tool_result)}\n\n"
                        "You are still working on the user's current request. "
                        "If the request is not fully completed yet, call the next required tool now. "
                        "Do not stop after read/search/list tools when an edit was requested. "
                        "For git requests, execute git steps yourself with tools instead of asking the user to run commands. "
                        "Only claim edits when apply_diff/write_file/delete_path succeeded with ok=true. "
                        "If blocked, explain briefly and ask one concise follow-up."
                    ),
                }
            )


if __name__ == "__main__":
    main()
