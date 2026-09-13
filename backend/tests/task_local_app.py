"""本地联调进程入口：真实 HTTP/认证/任务业务，仅替换数据库位置及无关上游。"""

import os

from celery import signals

from agent_lab.tasks import process
from agent_lab.tasks.celery_app import app
from tests.task_queue_support import database


class LocalProcessRuntime(process.ProcessRuntime):
    """保持生产事件循环与任务装配，把所有业务事务指向本次随机 schema。"""

    async def _open(self):
        from agent_lab.task_assembly import build_task_components

        self.engine, sessions = database(os.environ["TASK_TEST_DATABASE_URL"], os.environ["TASK_TEST_SCHEMA"])
        self.service, self.worker, self.dispatcher, self.store = build_task_components(
            sessions, owner=f"local-task:{os.getpid()}",
        )

    def close(self):
        super().close()
        print(f"task_local_runtime_closed pid={self.pid}", flush=True)


process.ProcessRuntime = LocalProcessRuntime


@signals.task_postrun.connect(weak=False)
def record_processed(task_id=None, **_kwargs):
    print(f"task_local_processed task_id={task_id}", flush=True)


def create_api():
    """在导入 HTTP 装配前置换数据库，使账号、Token 和业务都无法落入 public。"""
    from agent_lab.db import session as db

    db.engine, db.async_session_factory = database(
        os.environ["TASK_TEST_DATABASE_URL"], os.environ["TASK_TEST_SCHEMA"],
    )
    from agent_lab.task_assembly import build_task_components
    from tests.app_helpers import FakeSearchRuntime, create_offline_app

    service = build_task_components(db.async_session_factory)[0]
    # 保留真实账号密码/Cookie；搜索、Agent 和环境管理员同步不属于这次联调。
    return create_offline_app(runtime_factory=FakeSearchRuntime, task_service_factory=lambda: service)
