import os
from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path")
    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # return relative-ish path for display (keep user’s input)
        return {
            "ok": True,
            "path": path,
            "content": content,
            "bytes": len(content.encode("utf-8")),
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"file not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
