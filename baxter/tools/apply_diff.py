import difflib

from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path")
    find = args.get("find")
    replace = args.get("replace", "")
    replace_all = bool(args.get("replace_all", False))

    if not isinstance(path, str) or path.strip() == "":
        return {"ok": False, "error": "missing/invalid path"}
    if not isinstance(find, str) or find == "":
        return {"ok": False, "error": "missing/invalid find text"}
    if not isinstance(replace, str):
        return {"ok": False, "error": "replace must be a string"}

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        return {"ok": False, "error": f"file not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    hits = original.count(find)
    if hits == 0:
        return {"ok": False, "error": f"find text not found in: {path}"}
    if not replace_all and hits > 1:
        return {
            "ok": False,
            "error": (
                f"find text matched {hits} locations in {path}; "
                "set replace_all=true or provide a more specific find block"
            ),
        }

    if replace_all:
        updated = original.replace(find, replace)
        replacements = hits
    else:
        updated = original.replace(find, replace, 1)
        replacements = 1

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    added = 0
    removed = 0
    for line in diff_lines:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "path": path,
        "replacements": replacements,
        "added_lines": added,
        "removed_lines": removed,
        "bytes_before": len(original.encode("utf-8")),
        "bytes_after": len(updated.encode("utf-8")),
        "diff": "\n".join(diff_lines),
        "diff_available": True,
    }
