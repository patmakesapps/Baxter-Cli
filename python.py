import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

from tools.registry import render_registry_for_prompt, run_tool, TOOL_NAMES

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


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


def print_tool_event(tool_call: dict) -> None:
    tool_name = tool_call.get("tool", "unknown")
    print(f"⟡ Tool: {tool_name}")


def print_tool_result(tool_result: dict) -> None:
    ok = bool(tool_result.get("ok"))
    status = "ok" if ok else "error"
    print(f"☑ Result: {status}")

    if "cmd" in tool_result:
        print(f"  cmd: {' '.join(tool_result['cmd'])}")
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


def main():
    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    print("Has GROQ_API_KEY:", bool(os.getenv("GROQ_API_KEY")))
    print("Type 'exit' to quit.\n")

    while True:
        user_text = input("▣ You:").strip()
        if user_text.lower() in {"exit", "quit"}:
            break

        messages.append({"role": "user", "content": user_text})

        # Tool-chaining loop:
        # model -> (optional tool) -> model -> (optional tool) -> ... -> final text
        while True:
            reply = call_model(last_n_turns(messages, 6))
            messages.append({"role": "assistant", "content": reply})

            tool_call = try_parse_tool_call(reply)

            # No tool call => done for this user input
            if not tool_call:
                print("▢ Baxter:", reply)
                break

            # Tool call => run it and feed result back
            print_tool_event(tool_call)
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
