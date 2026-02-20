# Terminal Coding Agent (Groq + Local Tools)

A lightweight CLI coding assistant that uses the Groq Chat Completions API and a structured local tool registry to safely interact with your project directory, run restricted terminal commands, and execute controlled Git operations.

## What It Does

- Runs a chat loop in the terminal (`python.py`)
- Calls Groq (`llama-3.1-8b-instant` by default)
- Lets the model optionally call local tools:
  - `read_file`
  - `write_file`
  - `list_dir`
  - `make_dir`
  - `delete_path`
  - `run_cmd` (restricted terminal commands)
  - `git_cmd` (restricted Git commands)
- Supports multi-step tool chaining
- Prevents path escape and disallows absolute paths
- Prevents shell execution and unsafe command usage

---

## Project Structure

```text
.
├─ python.py
├─ .env.example
├─ README.md
└─ tools/
   ├─ registry.py
   ├─ safe_path.py
   ├─ read_file.py
   ├─ write_file.py
   ├─ list_dir.py
   ├─ make_dir.py
   ├─ delete_path.py
   ├─ run_cmd.py
   └─ git_cmd.py
```

---

## Requirements

- Python 3.9+
- A Groq API key
- Python package:
  - `python-dotenv`

---

## Setup

1. Create and activate a virtual environment (optional but recommended).

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Windows (cmd):

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependency:

```bash
pip install python-dotenv
```

3. Create `.env` from `.env.example` and set your key:

```env
GROQ_API_KEY=your_real_key_here
```

---

## Run

```bash
python python.py
```

You should see:

- `Has GROQ_API_KEY: True`
- `Type 'exit' to quit.`

---

## How Tool Calling Works

- The system prompt includes a full tool registry and strict JSON tool-call format.
- If the model responds with:

```json
{"tool":"read_file","args":{"path":"example.txt"}}
```

the app executes that tool and feeds the result back into the conversation.
- Tool chaining continues until the model returns normal text.
- The model cannot claim it created/modified/deleted anything unless the tool returns `"ok": true`.

---

## Terminal Tool (`run_cmd`)

Allows restricted execution of terminal commands inside the project root.

### Allowed binaries (default)

- `python`
- `python3`
- `pip`
- `pip3`
- `git`

### Example

```json
{"tool":"run_cmd","args":{"cmd":["python","--version"]}}
```

Security protections:

- No shell execution (`shell=False`)
- No command chaining
- Timeout enforced
- Working directory restricted to project root
- Only whitelisted binaries allowed

---

## Git Tool (`git_cmd`)

Allows restricted Git operations safely inside the project root.

### Allowed Git subcommands

- status
- log
- diff
- show
- branch
- switch
- checkout
- add
- commit
- push
- pull
- fetch
- remote
- rev-parse
- restore
- rm
- mv
- stash

### Example

```json
{"tool":"git_cmd","args":{"subcommand":"status","args":["-sb"]}}
```

Security protections:

- Only approved subcommands allowed
- Dangerous flags blocked
- Cannot override git working directory
- No shell execution
- Timeout enforced

---

## Safety Notes

### File Safety

- All tool paths are resolved relative to the current working directory.
- Absolute paths are rejected.
- `..` path traversal is rejected.
- Tools cannot escape the repository directory.

### Terminal Safety

- Only whitelisted binaries allowed.
- No shell usage.
- No piping or chaining.
- Timeout limits enforced.

### Git Safety

- Only approved Git subcommands.
- Restricted flags.
- No environment overrides.
- Controlled execution context.

---

## Common Issues

- `GROQ_API_KEY is missing. Put it in .env and restart.`
  - Ensure `.env` exists in repo root and includes `GROQ_API_KEY=...`
  - Restart your terminal after editing `.env`

- `git not found on PATH`
  - Install Git: https://git-scm.com/
  - Restart your terminal

- HTTP errors from Groq
  - Verify key validity
  - Verify model name
  - Check network connectivity

---

## Next Improvements (Optional)

- Add `requirements.txt`
- Add tests for tool safety and registry behavior
- Add streaming responses
- Add richer tool schema validation
- Add audit logging for terminal/Git actions
- Add npm/node support
- Add interactive approval mode before push
- Add commit message linting