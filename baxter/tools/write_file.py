import os
from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))

    if not isinstance(content, str):
        return {"ok": False, "error": "content must be a string"}

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Refuse to overwrite an existing file unless explicitly allowed
    if os.path.exists(full_path) and not overwrite:
        return {
            "ok": False,
            "error": f"refusing to overwrite existing file without overwrite=true: {path}",
        }

    # Also refuse empty writes unless explicitly overwriting (prevents accidental zeroing)
    if os.path.exists(full_path) and content == "" and not overwrite:
        return {
            "ok": False,
            "error": f"refusing to write empty content to existing file without overwrite=true: {path}",
        }

    # Ensure parent directory exists (only inside root)
    parent = os.path.dirname(full_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}
