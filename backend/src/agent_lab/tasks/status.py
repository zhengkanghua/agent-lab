"""Beat 本地就绪文件；检查不访问数据库、Redis 或业务依赖。"""

import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def status_path():
    return Path(os.environ.get("TASK_BEAT_STATUS_PATH", str(Path(tempfile.gettempdir()) / "agent-lab-beat.json")))


def write_status(*, ready, jobs, max_age_seconds=30):
    path = status_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "ready": ready, "jobs": jobs,
        "updated_at": time.time(), "max_age_seconds": max_age_seconds}), encoding="utf-8")
    temporary.replace(path)


def process_exists(pid):
    if pid <= 0:
        return False
    if os.name != "nt":
        os.kill(pid, 0)
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def check_status():
    try:
        state = json.loads(status_path().read_text(encoding="utf-8"))
        return state["ready"] is True and 0 <= time.time() - state["updated_at"] < state["max_age_seconds"] and process_exists(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(0 if check_status() else 1)
