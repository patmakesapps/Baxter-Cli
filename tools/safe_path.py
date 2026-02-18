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
