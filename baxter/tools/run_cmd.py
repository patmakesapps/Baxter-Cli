import os
import subprocess
from .safe_path import resolve_in_root


# Keep this intentionally conservative. Expand only when you need to.
ALLOWED_BINS = {
    "python",
    "python3",
    "pip",
    "pip3",
    "git",
}


def _is_list_of_strings(x):
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def run(args: dict) -> dict:
    """
    Run a command in the project root (no shell).
    Args:
      - cmd: list[str] (example: ["python", "--version"])
      - cwd: string relative path (optional, default ".")
      - timeout_sec: int (optional, default 60)
    """
    cmd = args.get("cmd")
    cwd = args.get("cwd", ".")
    timeout_sec = args.get("timeout_sec", 60)

    if not _is_list_of_strings(cmd) or len(cmd) == 0:
        return {
            "ok": False,
            "error": 'cmd must be a non-empty list of strings, e.g. ["git","status"]',
        }

    bin_name = cmd[0].strip()
    if bin_name not in ALLOWED_BINS:
        return {
            "ok": False,
            "error": f'command not allowed: "{bin_name}". Allowed: {sorted(ALLOWED_BINS)}',
        }

    if not isinstance(cwd, str) or cwd.strip() == "":
        cwd = "."

    try:
        full_cwd = resolve_in_root(cwd)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not isinstance(timeout_sec, int) or timeout_sec < 1 or timeout_sec > 300:
        timeout_sec = 60

    try:
        proc = subprocess.run(
            cmd,
            cwd=full_cwd,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            shell=False,
            env=os.environ.copy(),
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
            "error": f"command timed out after {timeout_sec}s",
            "cmd": cmd,
            "cwd": cwd,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f'command not found: "{bin_name}"'}
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": cmd, "cwd": cwd}
