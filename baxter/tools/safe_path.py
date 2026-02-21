import os


def resolve_in_root(path: str) -> str:
    """
    Resolve a user-provided relative path safely within the current working directory (root).
    Returns an absolute path inside root, or raises ValueError.
    """
    if not isinstance(path, str) or path.strip() == "":
        raise ValueError("missing/invalid path")

    # Disallow absolute paths outright
    if os.path.isabs(path):
        raise ValueError("absolute paths are not allowed in this demo")

    root = os.path.realpath(os.getcwd())
    target = os.path.realpath(os.path.join(root, path))

    # Ensure target stays inside root (prevents ../ escape)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("path escapes root folder")

    return target


def _project_relpath(abs_path: str) -> str:
    root = os.path.realpath(os.getcwd())
    rel = os.path.relpath(os.path.realpath(abs_path), root)
    return rel.replace("\\", "/")


def _find_files_by_basename(root: str, basename: str, limit: int = 25) -> list[str]:
    matches: list[str] = []
    target = basename.lower()

    for base, dirs, files in os.walk(root, topdown=True):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if name.lower() != target:
                continue
            matches.append(os.path.join(base, name))
            if len(matches) >= limit:
                return matches
    return matches


def resolve_file_path_in_root(path: str) -> tuple[str | None, list[str]]:
    """
    Resolve a user-provided file path safely within root.
    If the exact relative path does not exist, attempt a basename search and:
    - return (unique_match_abs_path, []) when exactly one file matches
    - return (None, [candidate_relpaths...]) when multiple or zero matches
    """
    full_path = resolve_in_root(path)
    if os.path.isfile(full_path):
        return full_path, []

    root = os.path.realpath(os.getcwd())
    basename = os.path.basename(path or "").strip()
    if basename == "":
        return None, []

    matches = _find_files_by_basename(root, basename)
    if len(matches) == 1:
        return matches[0], []
    return None, [_project_relpath(m) for m in matches]
