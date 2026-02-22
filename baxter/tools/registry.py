from .write_file import run as write_file_run
from .read_file import run as read_file_run
from .list_dir import run as list_dir_run
from .make_dir import run as make_dir_run
from .delete_path import run as delete_path_run
from .run_cmd import run as run_cmd_run
from .git_cmd import run as git_cmd_run
from .search_code import run as search_code_run
from .apply_diff import run as apply_diff_run

TOOL_REGISTRY = {
    "write_file": {
        "description": "Write a text file to disk.",
        "args": {
            "path": 'string (example: "dummy.py")',
            "content": "string (full file contents)",
            "overwrite": "bool (optional; set true when updating an existing file)",
        },
        "runner": write_file_run,
    },
    "apply_diff": {
        "description": "Apply a targeted text diff (find/replace) to an existing file.",
        "args": {
            "path": 'string (example: "baxter/providers.py")',
            "find": "string (exact text block to replace)",
            "replace": "string (replacement text block)",
            "replace_all": "bool (optional; default false, replaces only one match)",
        },
        "runner": apply_diff_run,
    },
    "read_file": {
        "description": "Read a text file from disk and return its contents.",
        "args": {
            "path": 'string (example: "styles.css")',
        },
        "runner": read_file_run,
    },
    "list_dir": {
        "description": "List files and folders in a directory (relative paths only).",
        "args": {
            "path": 'string (example: "." or "tools")',
        },
        "runner": list_dir_run,
    },
    "make_dir": {
        "description": "Create a directory (and parents) safely within the root.",
        "args": {
            "path": 'string (example: "website" or "src/assets")',
        },
        "runner": make_dir_run,
    },
    "delete_path": {
        "description": "Delete a file or directory safely within the root.",
        "args": {
            "path": 'string (example: "website.json" or "empty_folder")',
            "recursive": "bool (optional; default true for directory trees)",
        },
        "runner": delete_path_run,
    },
    "run_cmd": {
        "description": "Run an allowed terminal command in the project root (no shell), or stop a tracked detached process.",
        "args": {
            "cmd": 'list[string] (required unless stop_pid is set; example: ["python","--version"] or ["npm","run","dev"])',
            "cwd": 'string (optional, relative path; example: "." or "tools")',
            "timeout_sec": "int (optional, 1-1800; default adaptive 60->1800)",
            "detach": "bool (optional; default false; start command in background and return pid)",
            "stop_pid": "int (optional; stop a detached pid started by this Baxter session)",
        },
        "runner": run_cmd_run,
    },
    "git_cmd": {
        "description": "Run a restricted git command safely in the project root.",
        "args": {
            "subcommand": 'string (example: "status", "add", "commit", "push")',
            "args": 'list[string] (optional; example: ["-sb"] or ["-m","msg"])',
            "cwd": 'string (optional, relative path; default ".")',
            "timeout_sec": "int (optional, 1-300; default 60)",
        },
        "runner": git_cmd_run,
    },
    "search_code": {
        "description": "Search code/files recursively for text matches.",
        "args": {
            "query": 'string (required; example: "render_registry_for_prompt")',
            "path": 'string (optional, relative path; default ".")',
            "case_sensitive": "bool (optional; default false)",
            "max_results": "int (optional, 1-200; default 50)",
            "include_hidden": "bool (optional; default false)",
        },
        "runner": search_code_run,
    },
}


TOOL_NAMES = list(TOOL_REGISTRY.keys())


def render_registry_for_prompt() -> str:
    lines = ["TOOL REGISTRY:"]
    for name, spec in TOOL_REGISTRY.items():
        lines.append(f"- {name}: {spec['description']}")
        lines.append("  args:")
        for k, v in spec["args"].items():
            lines.append(f"    - {k}: {v}")
    return "\n".join(lines)


def run_tool(tool_name: str, args: dict) -> dict:
    spec = TOOL_REGISTRY.get(tool_name)
    if not spec:
        return {"ok": False, "error": f"unknown tool: {tool_name}"}
    try:
        return spec["runner"](args)
    except Exception as e:
        return {"ok": False, "error": str(e)}
