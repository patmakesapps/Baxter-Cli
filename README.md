# Terminal Coding Agent (Groq + Local Tools)

A lightweight CLI coding assistant that uses the Groq Chat Completions API and a small local tool registry to read/write files safely inside the current project folder.

## What It Does

- Runs a chat loop in the terminal (`python.py`)
- Calls Groq (`llama-3.1-8b-instant` by default)
- Lets the model optionally call local tools:
  - `read_file`
  - `write_file`
  - `list_dir`
- Prevents path escape and disallows absolute paths

## Project Structure

```text
.
├─ python.py
├─ .env.example
└─ tools/
   ├─ registry.py
   ├─ safe_path.py
   ├─ read_file.py
   ├─ write_file.py
   └─ list_dir.py
```

## Requirements

- Python 3.9+
- A Groq API key
- Python package:
  - `python-dotenv`

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

## Run

```bash
python python.py
```

You should see:

- `Has GROQ_API_KEY: True`
- `Type 'exit' to quit.`

## How Tool Calling Works

- The system prompt includes a tool registry and strict JSON tool-call format.
- If the model responds with:

```json
{"tool":"read_file","args":{"path":"example.txt"}}
```

the app executes that tool and feeds the result back into the conversation.
- Tool chaining continues until the model returns normal text.

## Safety Notes

- All tool paths are resolved relative to the current working directory.
- Absolute paths are rejected.
- `..` path escapes are rejected.
- `write_file` refuses to overwrite existing files unless `overwrite=true`.

## Common Issues

- `GROQ_API_KEY is missing. Put it in .env and restart.`
  - Ensure `.env` exists in repo root and includes `GROQ_API_KEY=...`
- HTTP errors from Groq
  - Verify key validity, model name, and network access.

## Next Improvements (Optional)

- Add `requirements.txt`
- Add tests for tool safety and registry behavior
- Add streaming responses
- Add richer tool schema validation
