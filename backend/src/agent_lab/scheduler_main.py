"""独立 scheduler 入口及本容器就绪检查；检查不连接远程服务。"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

logger = logging.getLogger(__name__)


def status_path():
    return Path(os.environ.get("SCHEDULER_STATUS_PATH", str(Path(tempfile.gettempdir()) / "agent-lab-scheduler.json")))


def write_status(*, ready, jobs, max_age_seconds=30):
    path = status_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "ready": ready, "jobs": jobs, "updated_at": time.time(), "max_age_seconds": max_age_seconds}), encoding="utf-8")
    temporary.replace(path)


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        os.kill(pid, 0)
        return True
    # Windows 的 os.kill(pid, 0) 不是只读探测，使用只查询进程状态的句柄。
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
        return state["ready"] is True and 0 <= time.time() - state["updated_at"] < state.get("max_age_seconds", 30) and process_exists(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


async def main():
    from agent_lab.config.scheduler import get_scheduler_settings
    from agent_lab.db.session import engine
    from agent_lab.pipeline.assembly import build_document_processing_consumer, build_scheduler_runner

    if not get_scheduler_settings().enabled:
        raise RuntimeError("独立 scheduler 的调度开关未启用。")
    write_status(ready=False, jobs=0)
    scheduler = build_scheduler_runner(status_writer=write_status)
    documents = build_document_processing_consumer()
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    try:
        await scheduler.start()
        await documents.start()
        logger.info("scheduler 已加载配置并启动")
        await stopping.wait()
    finally:
        try:
            try:
                await documents.close()
            finally:
                await scheduler.close()
        finally:
            await engine.dispose()


def entrypoint():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return 0 if check_status() else 1
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("agent_lab").setLevel(logging.INFO)
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(main())
        else:
            asyncio.run(main())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.error("scheduler 退出 error_type=%s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
