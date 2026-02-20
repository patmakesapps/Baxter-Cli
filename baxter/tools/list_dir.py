import os
from .safe_path import resolve_in_root


def run(args: dict) -> dict:
    path = args.get("path", ".")
    if not isinstance(path, str) or path.strip() == "":
        path = "."

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        entries = []
        for name in os.listdir(full_path):
            full = os.path.join(full_path, name)
            try:
                st = os.stat(full)
                entries.append(
                    {
                        "name": name,
                        "is_dir": os.path.isdir(full),
                        "size": (st.st_size if os.path.isfile(full) else None),
                    }
                )
            except Exception:
                entries.append({"name": name, "is_dir": os.path.isdir(full), "size": None})

        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return {"ok": True, "path": path, "entries": entries, "count": len(entries)}
    except FileNotFoundError:
        return {"ok": False, "error": f"directory not found: {path}"}
    except NotADirectoryError:
        return {"ok": False, "error": f"not a directory: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
