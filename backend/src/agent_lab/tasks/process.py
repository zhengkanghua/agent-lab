"""每个 Beat／prefork 子进程拥有一个持久事件循环与独立数据库池。"""

import asyncio
import os
import socket
import sys
from uuid import uuid4


class ProcessRuntime:
    def __init__(self):
        self.pid = os.getpid()
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        self.runner = asyncio.Runner(loop_factory=factory)
        self.engine = None
        self.worker = None
        self.service = None
        self.dispatcher = None
        self.store = None
        try:
            self.run(self._open())
        except BaseException:
            self.close()
            raise

    def run(self, coroutine):
        return self.runner.run(coroutine)

    async def _open(self):
        # 延迟到所属子进程导入，Celery 父进程不构造业务 Runtime 或使用异步连接。
        from agent_lab.db.session import build_database_resources
        from agent_lab.task_assembly import build_task_components
        self.engine, sessions = build_database_resources()
        self.service, self.worker, self.dispatcher, self.store = build_task_components(
            sessions, owner=f"{socket.gethostname()}:{self.pid}:{uuid4().hex}",
        )

    def close(self):
        try:
            if self.engine is not None:
                self.run(self.engine.dispose())
        finally:
            self.runner.close()


_runtime = None


def get_process_runtime():
    global _runtime
    if _runtime is None or _runtime.pid != os.getpid():
        _runtime = ProcessRuntime()
    return _runtime


def reset_after_fork(**kwargs):
    global _runtime
    _runtime = None


def close_process_runtime(**kwargs):
    global _runtime
    if _runtime is not None and _runtime.pid == os.getpid():
        _runtime.close()
    _runtime = None
