import os
from .safe_path import resolve_file_path_in_root


def run(args: dict) -> dict:
    path = args.get("path")
    if not isinstance(path, str) or path.strip() == "":
        return {"ok": False, "error": "missing/invalid path"}
    if path.strip() in {".", "./", ".\\"}:
        return {
            "ok": False,
            "error": 'path "." is a directory; use list_dir for directories or provide a file path',
        }
    try:
        full_path, candidates = resolve_file_path_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if full_path is None:
        if candidates:
            return {
                "ok": False,
                "error": f'path "{path}" is ambiguous; provide a more specific relative path',
                "candidates": candidates,
            }
        return {"ok": False, "error": f"file not found: {path}"}

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # return relative-ish path for display (keep user’s input)
        return {
            "ok": True,
            "path": path,
            "resolved_path": os.path.relpath(full_path, os.getcwd()).replace("\\", "/"),
            "content": content,
            "bytes": len(content.encode("utf-8")),
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"file not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
