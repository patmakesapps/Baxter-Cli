# Terminal Coding Agent (Baxter CLI)

![Baxter CLI Banner](baxter.png)

A local terminal coding assistant with provider switching, tool-calling, and safety rails for file + command operations.

## Features

- Interactive chat loop with tool chaining
- Provider support:
  - `anthropic` (`/v1/messages`)
  - `openai` (`/v1/responses`)
  - `groq` (OpenAI-compatible `chat/completions`)
- Startup provider preference: `anthropic` -> `openai` -> `groq`
- Curated model lists per provider (with OpenAI dynamic filtering against `/v1/models`)
- Working indicator while model calls are in flight (`Baxter is working...`)
- Built-in malformed tool-call recovery (one automatic retry if JSON tool call is broken)

## Current Model Sets

- `anthropic`
  - `claude-opus-4-6`
  - `claude-sonnet-4-6`
  - `claude-haiku-4-5-20251001` (default)
- `openai`
  - `gpt-4o-mini` (default)
  - `gpt-5-mini`
  - `codex-3.5`
- `groq`
  - `llama-3.1-8b-instant` (default)

Notes:
- OpenAI model IDs are fetched from `/v1/models` and intersected with the allowlist above.
- You can override the OpenAI allowlist with `OPENAI_MODELS_ALLOWLIST` (comma-separated IDs).

## Tooling

Tools available:
- `read_file`
- `write_file`
- `apply_diff`
- `list_dir`
- `make_dir`
- `delete_path`
- `run_cmd`
- `git_cmd`
- `search_code`

Key behaviors:
- File paths are restricted to the repo root (no absolute paths, no `..` escape).
- `delete_path` supports recursive directory deletion (default `recursive=true`).
- `write_file` refuses to overwrite existing files unless `overwrite=true`.
- `apply_diff` supports targeted edits using exact `find`/`replace` with optional `replace_all=true`.
- `apply_diff` returns a unified diff summary (`+/-`) and stores the full last diff for terminal viewing.

## Confirmations

Baxter asks `y/N` confirmation before:
- `delete_path`
- `apply_diff`
- `write_file` when `overwrite=true`
- `git push`
- `git rm`

## CLI Commands

- `/` opens interactive provider/model picker
- `/providers` (alias: `/settings`)
- `/provider <groq|openai|anthropic>`
- `/models`
- `/model <model_name>`
- `/lastdiff` (expand the last `apply_diff` unified diff)
- `/help`

## Project Layout

```text
.
├─ .env.example
├─ pyproject.toml
├─ README.md
└─ baxter/
   ├─ __init__.py
   ├─ baxter_cli.py
   ├─ providers.py
   └─ tools/
      ├─ __init__.py
      ├─ registry.py
      ├─ safe_path.py
      ├─ read_file.py
      ├─ write_file.py
      ├─ apply_diff.py
      ├─ list_dir.py
      ├─ make_dir.py
      ├─ delete_path.py
      ├─ run_cmd.py
      ├─ git_cmd.py
      └─ search_code.py
```

## Requirements

- Python 3.10+
- At least one API key:
  - `ANTHROPIC_API_KEY`
  - `OPENAI_API_KEY`
  - `GROQ_API_KEY`

## Setup (Developer)

1. Create and activate a virtual environment.

Windows (cmd):

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install editable:

```bash
pip install -e .
```

3. Create `.env` from `.env.example` and set keys:

```env
GROQ_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
# optional:
# OPENAI_MODELS_ALLOWLIST=gpt-4o-mini,gpt-5-mini,codex-3.5
```

The CLI also loads user-level keys from `~/.baxter/.env` first, then applies project `.env` as an override.

## Setup (User install via pip)

1. Install Baxter:

```bash
pip install baxter-cli
```

2. Configure keys once per machine in:

- Windows: `%USERPROFILE%\.baxter\.env`
- macOS/Linux: `~/.baxter/.env`

Example:

```env
GROQ_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
# optional:
# OPENAI_MODELS_ALLOWLIST=gpt-4o-mini,gpt-5-mini,codex-3.5
```

If keys are missing on startup, Baxter now offers an interactive one-time setup prompt and writes keys to `~/.baxter/.env`.

3. Open any project folder and run:

```bash
baxter
```

## Environment Setup Smoke Test

Use this to verify first-run key loading and precedence.

1. Missing keys path:

```powershell
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
Remove-Item "$HOME\.baxter\.env" -ErrorAction SilentlyContinue
baxter
```

Expected: startup warning about missing keys.

2. User-level key file path:

```powershell
mkdir $HOME\.baxter -Force
@"
OPENAI_API_KEY=your_real_key
"@ | Set-Content "$HOME\.baxter\.env"
baxter
```

Expected: no missing-key warning.

3. Project-level override path:

```powershell
@"
GROQ_API_KEY=your_real_groq_key
"@ | Set-Content ".env"
baxter
```

Expected: local `.env` values override user-level values for overlapping keys.

## Run

```bash
baxter
```

or:

```bash
python -m baxter.baxter_cli
```

## Command Safety Model

`run_cmd` allowlist:
- `python`
- `python3`
- `pip`
- `pip3`
- `git`

`git_cmd` subcommand allowlist:
- `status`
- `log`
- `diff`
- `show`
- `branch`
- `switch`
- `checkout`
- `add`
- `commit`
- `push`
- `pull`
- `fetch`
- `remote`
- `rev-parse`
- `restore`
- `rm`
- `mv`
- `stash`

Additional protections:
- No shell execution for command tools
- Path traversal/root escape blocked
- Selected risky git flags blocked (`--git-dir`, `--work-tree`, `-C`, etc.)
- Per-tool timeout bounds

## Troubleshooting

- Missing key error:
  - Verify `.env` has the expected API key and restart Baxter.
- OpenAI tool-call/JSON issues:
  - Baxter now does one automatic repair retry for malformed tool-call JSON.
- OpenAI model list too large:
  - Set `OPENAI_MODELS_ALLOWLIST` explicitly.
- `git not found on PATH`:
  - Install Git and restart terminal.
