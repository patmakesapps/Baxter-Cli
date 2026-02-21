import os
import shutil
import subprocess

from .safe_path import resolve_in_root


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    return default


def _normalize_max_results(value, default=50):
    if not isinstance(value, int):
        return default
    if value < 1:
        return 1
    if value > 200:
        return 200
    return value


def _project_relpath(abs_path: str) -> str:
    root = os.path.realpath(os.getcwd())
    rel = os.path.relpath(os.path.realpath(abs_path), root)
    return rel.replace("\\", "/")


def _parse_rg_line(line: str):
    parts = line.split(":", 3)
    if len(parts) != 4:
        return None
    file_part, line_part, col_part, text_part = parts
    try:
        line_num = int(line_part)
        col_num = int(col_part)
    except Exception:
        return None
    return file_part, line_num, col_num, text_part


def _search_with_rg(full_path: str, query: str, case_sensitive: bool, max_results: int, include_hidden: bool):
    cmd = ["rg", "--no-heading", "--line-number", "--column", query, "."]
    if not case_sensitive:
        cmd.insert(1, "-i")
    if include_hidden:
        cmd.insert(1, "--hidden")
    cmd.extend(["--glob", "!.git"])

    proc = subprocess.run(
        cmd,
        cwd=full_path,
        text=True,
        capture_output=True,
        timeout=60,
        shell=False,
    )
    if proc.returncode not in (0, 1):
        return {"ok": False, "error": f"rg failed: {(proc.stderr or '').strip()}"}

    matches = []
    truncated = False
    for raw_line in (proc.stdout or "").splitlines():
        parsed = _parse_rg_line(raw_line)
        if not parsed:
            continue
        file_part, line_num, col_num, text_part = parsed
        abs_file = os.path.join(full_path, file_part)
        matches.append(
            {
                "file": _project_relpath(abs_file),
                "line": line_num,
                "column": col_num,
                "text": text_part,
            }
        )
        if len(matches) >= max_results:
            truncated = True
            break

    return {"ok": True, "matches": matches, "truncated": truncated, "engine": "rg"}


def _search_with_python(full_path: str, query: str, case_sensitive: bool, max_results: int, include_hidden: bool):
    needle = query if case_sensitive else query.lower()
    matches = []
    truncated = False

    for base, dirs, files in os.walk(full_path, topdown=True):
        dirs[:] = [d for d in dirs if d != ".git"]
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]

        for filename in files:
            if not include_hidden and filename.startswith("."):
                continue

            abs_file = os.path.join(base, filename)
            try:
                with open(abs_file, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        haystack = line if case_sensitive else line.lower()
                        pos = haystack.find(needle)
                        if pos == -1:
                            continue
                        matches.append(
                            {
                                "file": _project_relpath(abs_file),
                                "line": idx,
                                "column": pos + 1,
                                "text": line.rstrip("\n"),
                            }
                        )
                        if len(matches) >= max_results:
                            truncated = True
                            return {"ok": True, "matches": matches, "truncated": truncated, "engine": "python"}
            except Exception:
                continue

    return {"ok": True, "matches": matches, "truncated": truncated, "engine": "python"}


def run(args: dict) -> dict:
    query = args.get("query")
    path = args.get("path", ".")
    case_sensitive = _as_bool(args.get("case_sensitive"), default=False)
    include_hidden = _as_bool(args.get("include_hidden"), default=False)
    max_results = _normalize_max_results(args.get("max_results"), default=50)

    if not isinstance(query, str) or query.strip() == "":
        return {"ok": False, "error": "missing/invalid query"}
    if not isinstance(path, str) or path.strip() == "":
        path = "."

    try:
        full_path = resolve_in_root(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not os.path.isdir(full_path):
        return {"ok": False, "error": f"not a directory: {path}"}

    try:
        if shutil.which("rg"):
            result = _search_with_rg(full_path, query, case_sensitive, max_results, include_hidden)
        else:
            result = _search_with_python(full_path, query, case_sensitive, max_results, include_hidden)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "search timed out after 60s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not result.get("ok"):
        return result

    return {
        "ok": True,
        "path": path,
        "query": query,
        "case_sensitive": case_sensitive,
        "include_hidden": include_hidden,
        "max_results": max_results,
        "matches": result["matches"],
        "count": len(result["matches"]),
        "truncated": bool(result.get("truncated")),
        "engine": result.get("engine"),
    }
