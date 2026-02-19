import os
from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path")
    if not isinstance(path, str) or path.strip() == "":
        return {"ok": False, "error": "missing/invalid path"}

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        if not os.path.exists(full_path):
            return {"ok": False, "error": f"not found: {path}"}

        if os.path.isdir(full_path):
            # only delete empty dirs (safe default)
            os.rmdir(full_path)
            return {"ok": True, "path": path, "deleted": "dir"}
        else:
            os.remove(full_path)
            return {"ok": True, "path": path, "deleted": "file"}
    except OSError as e:
        # commonly "directory not empty"
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
