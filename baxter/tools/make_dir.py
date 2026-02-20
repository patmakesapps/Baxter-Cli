import os
from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path", ".")
    if not isinstance(path, str) or path.strip() == "":
        return {"ok": False, "error": "missing/invalid path"}

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        os.makedirs(full_path, exist_ok=True)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}
