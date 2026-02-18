from .write_file import run as write_file_run
from .read_file import run as read_file_run
from .list_dir import run as list_dir_run

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
