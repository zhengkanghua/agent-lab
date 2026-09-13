"""仅测试子进程导入的 Celery 装配，不向生产增加测试开关或动态代码加载。"""

import os
from celery import signals

from agent_lab.tasks import process
from agent_lab.tasks.celery_app import app
from agent_lab.task_assembly import publish_message
from tests.task_queue_support import components, database


class QueueProcessRuntime(process.ProcessRuntime):
    async def _open(self):
        self.engine, sessions = database(os.environ["TASK_TEST_DATABASE_URL"], os.environ["TASK_TEST_SCHEMA"])
        self.service, self.worker, self.dispatcher, self.store = components(sessions, publish_message, owner=f"queue-test:{os.getpid()}")


process.ProcessRuntime = QueueProcessRuntime
# Redis 实例也属于本夹具；前缀和队列进一步隔离消息及 Celery 控制键。
app.conf.broker_transport_options["global_keyprefix"] = os.environ["TASK_TEST_SCHEMA"] + ":"


@signals.task_received.connect(weak=False)
def record_delivery(request=None, **_kwargs):
    # 观察真实 broker 的重投标记，而不靠等待超过 timeout 就声称已发生重投。
    if request is not None and request.name == "agent_lab.execute":
        print(f"queue_probe_delivery run_id={request.args[0]} redelivered={bool(request.delivery_info.get('redelivered'))}", flush=True)


@signals.task_postrun.connect(weak=False)
def record_processed(task_id=None, sender=None, **_kwargs):
    if sender is not None and sender.name == "agent_lab.execute":
        print(f"queue_probe_processed task_id={task_id}", flush=True)
