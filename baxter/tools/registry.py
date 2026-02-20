from .write_file import run as write_file_run
from .read_file import run as read_file_run
from .list_dir import run as list_dir_run
from .make_dir import run as make_dir_run
from .delete_path import run as delete_path_run
from .run_cmd import run as run_cmd_run
from .git_cmd import run as git_cmd_run

TOOL_REGISTRY = {
    "write_file": {
        "description": "Write a text file to disk.",
        "args": {
            "path": 'string (example: "dummy.py")',
            "content": "string (full file contents)",
        },
        "runner": write_file_run,
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
        "description": "Delete a file or an EMPTY directory safely within the root.",
        "args": {
            "path": 'string (example: "website.json" or "empty_folder")',
        },
        "runner": delete_path_run,
    },
    "run_cmd": {
        "description": "Run an allowed terminal command in the project root (no shell).",
        "args": {
            "cmd": 'list[string] (example: ["python","--version"] or ["pip","-V"])',
            "cwd": 'string (optional, relative path; example: "." or "tools")',
            "timeout_sec": "int (optional, 1-300; default 60)",
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
