import os
import signal
import socket
import subprocess
import threading
import time
from .safe_path import resolve_in_root


# Keep this intentionally conservative. Expand only when you need to.
ALLOWED_BINS = {
    "python",
    "python3",
    "pip",
    "pip3",
    "git",
    "node",
    "npm",
    "npx",
    # Windows command shims for better PATH compatibility.
    "python.exe",
    "pip.exe",
    "git.exe",
    "node.exe",
    "npm.cmd",
    "npx.cmd",
}

# Prefer Windows-native shim names when the bare name is unavailable.
WINDOWS_BIN_FALLBACKS = {
    "python": "python.exe",
    "pip": "pip.exe",
    "git": "git.exe",
    "node": "node.exe",
    "npm": "npm.cmd",
    "npx": "npx.cmd",
}

# Track only detached processes started by this Baxter session.
DETACHED_PIDS: set[int] = set()
ACTIVE_FOREGROUND_PROC: subprocess.Popen | None = None
ACTIVE_FOREGROUND_LOCK = threading.Lock()
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_AUTO_MAX_TIMEOUT_SEC = 1800
MAX_TIMEOUT_CEILING_SEC = 1800


def _is_list_of_strings(x):
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _command_candidates(cmd: list[str]) -> list[list[str]]:
    if not cmd:
        return []
    candidates = [cmd]
    if os.name != "nt":
        return candidates

    bin_name = cmd[0].strip()
    fallback = WINDOWS_BIN_FALLBACKS.get(bin_name.lower())
    if fallback and fallback != bin_name:
        alt = [fallback] + cmd[1:]
        candidates.append(alt)
    return candidates


def _normalize_timeout(raw_timeout) -> tuple[int, int, bool]:
    """
    Returns (soft_timeout_sec, max_timeout_sec, adaptive).
    - Explicit timeout values other than 60 are treated as strict.
    - 60 is treated as the common default and auto-extends to 1800.
    - Missing/invalid timeout also uses adaptive behavior.
    """
    configured_max = DEFAULT_AUTO_MAX_TIMEOUT_SEC

    if isinstance(raw_timeout, int) and 1 <= raw_timeout <= configured_max:
        if raw_timeout == DEFAULT_TIMEOUT_SEC:
            return DEFAULT_TIMEOUT_SEC, configured_max, True
        return raw_timeout, raw_timeout, False
    return DEFAULT_TIMEOUT_SEC, configured_max, True


def _stream_reader(stream, bucket: list[str], label: str, stream_output: bool) -> None:
    try:
        for line in iter(stream.readline, ""):
            bucket.append(line)
            if stream_output:
                text = line.rstrip("\r\n")
                if text:
                    print(f"\n  {label}: {text}", flush=True)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is not signalable by this user.
        return True
    except Exception:
        return False


def _spawn_kwargs() -> dict:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _set_active_foreground_proc(proc: subprocess.Popen | None) -> None:
    with ACTIVE_FOREGROUND_LOCK:
        global ACTIVE_FOREGROUND_PROC
        ACTIVE_FOREGROUND_PROC = proc


def stop_active_foreground_process() -> bool:
    """
    Best-effort stop for the currently running non-detached command.
    Returns True if a tracked process existed and a stop attempt was made.
    """
    with ACTIVE_FOREGROUND_LOCK:
        proc = ACTIVE_FOREGROUND_PROC
    if proc is None:
        return False
    _terminate_process_tree(proc)
    return True


def stop_all_tracked_processes() -> int:
    """
    Best-effort stop for all detached processes started by this Baxter session.
    Returns the number of tracked pids that were targeted.
    """
    pids = list(DETACHED_PIDS)
    for pid in pids:
        try:
            _stop_tracked_pid(pid)
        except Exception:
            # Keep shutdown resilient; continue stopping remaining pids.
            continue
    return len(pids)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                text=True,
                capture_output=True,
                timeout=10,
                shell=False,
                env=os.environ.copy(),
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return

    # POSIX: terminate entire process group created by start_new_session=True.
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            return
    time.sleep(0.2)
    if proc.poll() is None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _stop_tracked_pid(pid: int) -> dict:
    if pid not in DETACHED_PIDS:
        return {
            "ok": False,
            "error": f"pid {pid} is not tracked in this Baxter session",
            "pid": pid,
        }

    if not _pid_is_running(pid):
        DETACHED_PIDS.discard(pid)
        return {
            "ok": True,
            "success": True,
            "pid": pid,
            "stopped": True,
            "message": "process was already not running",
        }

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        DETACHED_PIDS.discard(pid)
        return {
            "ok": True,
            "success": True,
            "pid": pid,
            "stopped": True,
            "message": "process exited before stop request completed",
        }
    except PermissionError:
        return {
            "ok": False,
            "error": f"permission denied stopping pid {pid}",
            "pid": pid,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "pid": pid}

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _pid_is_running(pid):
            DETACHED_PIDS.discard(pid)
            return {
                "ok": True,
                "success": True,
                "pid": pid,
                "stopped": True,
                "message": "process stopped",
            }
        time.sleep(0.1)

    # Windows fallback: hard-kill process tree if it ignored SIGTERM.
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                text=True,
                capture_output=True,
                timeout=10,
                shell=False,
                env=os.environ.copy(),
            )
            stopped = proc.returncode == 0
            if stopped:
                DETACHED_PIDS.discard(pid)
            return {
                "ok": stopped,
                "success": stopped,
                "pid": pid,
                "stopped": stopped,
                "exit_code": proc.returncode,
                "stdout": (proc.stdout or ""),
                "stderr": (proc.stderr or ""),
                "message": "forced stop via taskkill" if stopped else "taskkill failed",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "pid": pid}

    # POSIX fallback.
    try:
        os.kill(pid, signal.SIGKILL)
        DETACHED_PIDS.discard(pid)
        return {
            "ok": True,
            "success": True,
            "pid": pid,
            "stopped": True,
            "message": "forced stop via SIGKILL",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "pid": pid}


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except Exception:
        return False


def _wait_for_port_ready(pid: int, port: int, timeout_sec: int) -> tuple[bool, bool]:
    """
    Wait until localhost:port is accepting connections.
    Returns (ready, process_running).
    """
    deadline = time.time() + max(1, timeout_sec)
    while time.time() < deadline:
        if not _pid_is_running(pid):
            return False, False
        if _port_is_open("127.0.0.1", port):
            return True, True
        time.sleep(0.2)
    return False, _pid_is_running(pid)


def run(args: dict) -> dict:
    """
    Run a command in the project root (no shell).
    Args:
      - cmd: list[str] (example: ["python", "--version"])
      - cwd: string relative path (optional, default ".")
      - timeout_sec: int (optional, 1-1800). Default uses adaptive behavior from 60s up to 1800.
      - detach: bool (optional, default false). If true, starts process and returns PID immediately.
      - stop_pid: int (optional). Stop a detached process started by this Baxter session.
    """
    stop_pid = args.get("stop_pid")
    if stop_pid is not None:
        try:
            pid = int(stop_pid)
            if pid <= 0:
                raise ValueError("pid must be > 0")
        except Exception:
            return {"ok": False, "error": "stop_pid must be a positive integer"}
        return _stop_tracked_pid(pid)

    cmd = args.get("cmd")
    cwd = args.get("cwd", ".")
    timeout_sec_raw = args.get("timeout_sec")
    detach = bool(args.get("detach", False))
    stream_output = bool(args.get("_stream_output", False))
    wait_for_ready = bool(args.get("_wait_for_ready", False))
    ready_port_raw = args.get("_ready_port", 3000)
    ready_timeout_raw = args.get("_ready_timeout_sec")

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

    soft_timeout_sec, max_timeout_sec, adaptive_timeout = _normalize_timeout(timeout_sec_raw)

    try:
        ready_port = int(ready_port_raw)
    except Exception:
        ready_port = 3000
    if ready_port < 1 or ready_port > 65535:
        ready_port = 3000

    try:
        ready_timeout_sec = int(ready_timeout_raw) if ready_timeout_raw is not None else min(max_timeout_sec, 180)
    except Exception:
        ready_timeout_sec = min(max_timeout_sec, 180)
    if ready_timeout_sec < 1:
        ready_timeout_sec = 1
    if ready_timeout_sec > max_timeout_sec:
        ready_timeout_sec = max_timeout_sec

    # If detached, timeout doesn't apply (we return immediately).
    if detach:
        if not wait_for_ready:
            soft_timeout_sec = 1
            max_timeout_sec = 1
            adaptive_timeout = False

    candidates = _command_candidates(cmd)
    tried_bins: list[str] = []

    try:
        if detach:
            for candidate in candidates:
                try:
                    proc = subprocess.Popen(
                        candidate,
                        cwd=full_cwd,
                        shell=False,
                        env=os.environ.copy(),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        **_spawn_kwargs(),
                    )
                    DETACHED_PIDS.add(proc.pid)
                    if wait_for_ready:
                        try:
                            ready, running = _wait_for_port_ready(
                                proc.pid, ready_port, ready_timeout_sec
                            )
                        except KeyboardInterrupt:
                            _stop_tracked_pid(proc.pid)
                            raise
                        return {
                            "ok": True,
                            "success": ready or running,
                            "cmd": candidate,
                            "cwd": cwd,
                            "detached": True,
                            "pid": proc.pid,
                            "ready": ready,
                            "ready_port": ready_port,
                            "ready_timeout_sec": ready_timeout_sec,
                            "message": (
                                f"process is running and ready on localhost:{ready_port}"
                                if ready
                                else f"process started in background; readiness not confirmed within {ready_timeout_sec}s"
                            ),
                        }
                    return {
                        "ok": True,
                        "success": True,
                        "cmd": candidate,
                        "cwd": cwd,
                        "detached": True,
                        "pid": proc.pid,
                        "message": "process started in background",
                    }
                except FileNotFoundError:
                    tried_bins.append(candidate[0])
            return {
                "ok": False,
                "error": f'command not found: "{bin_name}"',
                "tried": tried_bins or [bin_name],
                "cmd": cmd,
                "cwd": cwd,
            }

        for candidate in candidates:
            try:
                proc = subprocess.Popen(
                    candidate,
                    cwd=full_cwd,
                    text=True,
                    bufsize=1,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    env=os.environ.copy(),
                    **_spawn_kwargs(),
                )
                _set_active_foreground_proc(proc)
                stdout_chunks: list[str] = []
                stderr_chunks: list[str] = []
                t_out = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stdout, stdout_chunks, "stdout", stream_output),
                    daemon=True,
                )
                t_err = threading.Thread(
                    target=_stream_reader,
                    args=(proc.stderr, stderr_chunks, "stderr", stream_output),
                    daemon=True,
                )
                t_out.start()
                t_err.start()
                timeout_extended = False
                timed_out = False
                try:
                    proc.wait(timeout=soft_timeout_sec)
                except KeyboardInterrupt:
                    _terminate_process_tree(proc)
                    raise
                except subprocess.TimeoutExpired:
                    if adaptive_timeout and max_timeout_sec > soft_timeout_sec:
                        timeout_extended = True
                        try:
                            proc.wait(timeout=(max_timeout_sec - soft_timeout_sec))
                        except KeyboardInterrupt:
                            _terminate_process_tree(proc)
                            raise
                        except subprocess.TimeoutExpired:
                            timed_out = True
                            _terminate_process_tree(proc)
                    else:
                        timed_out = True
                        _terminate_process_tree(proc)
                finally:
                    _set_active_foreground_proc(None)

                if timed_out and proc.poll() is None:
                    _terminate_process_tree(proc)

                t_out.join(timeout=5)
                t_err.join(timeout=5)
                stdout_text = "".join(stdout_chunks)
                stderr_text = "".join(stderr_chunks)
                if timed_out and (not stderr_text.strip()):
                    stderr_text = "process timed out and did not flush output after termination"

                if timed_out:
                    return {
                        "ok": False,
                        "success": False,
                        "timed_out": True,
                        "timeout_extended": timeout_extended,
                        "timeout_sec": max_timeout_sec,
                        "timeout_policy": "adaptive" if adaptive_timeout else "fixed",
                        "error": (
                            f"command timed out after {max_timeout_sec}s"
                            if max_timeout_sec == soft_timeout_sec
                            else f"command timed out after {soft_timeout_sec}s and auto-extended to {max_timeout_sec}s"
                        ),
                        "cmd": candidate,
                        "cwd": cwd,
                        "stdout": (stdout_text or ""),
                        "stderr": (stderr_text or ""),
                    }

                return {
                    "ok": True,
                    "success": proc.returncode == 0,
                    "cmd": candidate,
                    "cwd": cwd,
                    "exit_code": proc.returncode,
                    "stdout": (stdout_text or ""),
                    "stderr": (stderr_text or ""),
                    "timeout_sec": max_timeout_sec if adaptive_timeout else soft_timeout_sec,
                    "timeout_policy": "adaptive" if adaptive_timeout else "fixed",
                    "timeout_extended": timeout_extended,
                }
            except FileNotFoundError:
                tried_bins.append(candidate[0])
        return {
            "ok": False,
            "error": f'command not found: "{bin_name}"',
            "tried": tried_bins or [bin_name],
            "cmd": cmd,
            "cwd": cwd,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "cmd": cmd, "cwd": cwd}
