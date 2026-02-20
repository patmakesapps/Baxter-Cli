import subprocess
from .safe_path import resolve_in_root


# Git subcommands we will allow the agent to run.
# Keep this tight — you can expand later.
ALLOWED_SUBCOMMANDS = {
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "switch",
    "checkout",
    "add",
    "commit",
    "push",
    "pull",
    "fetch",
    "remote",
    "rev-parse",
    "restore",
    "rm",
    "mv",
    "stash",
}


# Flags we refuse because they can run arbitrary programs or do surprising things.
DISALLOWED_TOKENS = {
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "-C",  # we control cwd ourselves
    "--paginate",  # not dangerous but can hang/behave oddly in some envs
    "--no-pager",  # fine usually, but leave it out for now
}


def _is_list_of_strings(x):
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def run(args: dict) -> dict:
    """
    Run a restricted git command in the project root (no shell).
    Args:
      - subcommand: string (example: "status")
      - args: list[str] (optional) (example: ["-sb"])
      - cwd: string relative path (optional, default ".")
      - timeout_sec: int (optional, default 60)
    """
    sub = args.get("subcommand")
    extra = args.get("args", [])
    cwd = args.get("cwd", ".")
    timeout_sec = args.get("timeout_sec", 60)

    if not isinstance(sub, str) or sub.strip() == "":
        return {"ok": False, "error": 'subcommand must be a string (example: "status")'}

    sub = sub.strip()
    if sub not in ALLOWED_SUBCOMMANDS:
        return {
            "ok": False,
            "error": f'git subcommand not allowed: "{sub}". Allowed: {sorted(ALLOWED_SUBCOMMANDS)}',
        }

    if extra is None:
        extra = []
    if not _is_list_of_strings(extra):
        return {
            "ok": False,
            "error": 'args must be a list of strings (example: ["-sb"])',
        }

    # Reject suspicious tokens
    for t in extra:
        if t in DISALLOWED_TOKENS:
            return {"ok": False, "error": f'disallowed token in git args: "{t}"'}
        if t.startswith("--upload-pack=") or t.startswith("--receive-pack="):
            return {
                "ok": False,
                "error": "disallowed git arg: upload-pack/receive-pack",
            }

    if not isinstance(cwd, str) or cwd.strip() == "":
        cwd = "."

    try:
        full_cwd = resolve_in_root(cwd)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not isinstance(timeout_sec, int) or timeout_sec < 1 or timeout_sec > 300:
        timeout_sec = 60

    cmd = ["git", sub] + extra

    try:
        proc = subprocess.run(
            cmd,
            cwd=full_cwd,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            shell=False,
        )

        return {
            "ok": True,
            "cmd": cmd,
            "cwd": cwd,
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or ""),
            "stderr": (proc.stderr or ""),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"git command timed out after {timeout_sec}s",
            "cmd": cmd,
            "cwd": cwd,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "git not found on PATH. Install Git and restart the terminal.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": cmd, "cwd": cwd}
