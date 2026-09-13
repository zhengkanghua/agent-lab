"""显式授权的 Linux prefork + Redis + PostgreSQL 验收，禁止 eager/内存代理替代。

每项测试创建随机 schema、独立 redis-server 及自己的 Beat/Worker 进程组；
只停止这些进程并清理本次资源。默认跳过，不读取生产 Redis 或业务上游。
"""

import asyncio
from datetime import UTC, datetime, timedelta
import os
import json
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

from celery import Celery
import pytest
import redis
from sqlalchemy import select, text, update

from agent_lab.db.base import Base
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord, TaskPolicyRecord
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.tasks.contracts import ExecutionPolicy
from agent_lab.tasks.cron import CronSchedule
from tests.task_queue_support import components, database, event_rows

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TASK_QUEUE_INTEGRATION_TEST") != "1" or sys.platform != "linux",
    reason="需明确授权 Linux 隔离 PostgreSQL/Redis 与 prefork 进程验收。",
)


class QueueEnvironment:
    """通过公开受理/查询观察结果；故障只注入本测试的消息与进程。"""

    def __init__(self, directory):
        self.directory = directory
        self.schema = f"task_queue_test_{uuid4().hex}"
        self.queue = self.schema
        self.children = []
        self.redis_process = None
        self.logs = []
        self.runner = asyncio.Runner()
        self.engine = None
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        self.url = f"redis://127.0.0.1:{self.port}/0"
        self.redis = redis.Redis.from_url(self.url, socket_timeout=1, socket_connect_timeout=1)
        self.app = Celery("queue_test_sender", broker=self.url)
        self.app.conf.update(task_default_queue=self.queue, task_ignore_result=True,
            broker_transport_options={"global_keyprefix": self.schema + ":", "visibility_timeout": 2,
                "socket_timeout": 1, "socket_connect_timeout": 1})

    def run(self, coroutine):
        return self.runner.run(coroutine)

    def open(self):
        dsn = os.environ.get("TASK_TEST_DATABASE_URL", "")
        if not dsn.startswith("postgresql+psycopg://"):
            pytest.fail("必须提供 TASK_TEST_DATABASE_URL（允许随机 schema 的测试 PostgreSQL）。", pytrace=False)
        if not shutil.which("redis-server"):
            pytest.fail("Linux 夹具需要 redis-server；可使用 docker-compose.task-tests.yml。", pytrace=False)
        self.env = {**os.environ, "TASK_TEST_SCHEMA": self.schema,
            "REDIS_URL": self.url, "REDIS_PASSWORD": "", "TASK_QUEUE_NAME": self.queue,
            "TASK_QUEUE_VISIBILITY_TIMEOUT": "2", "TASK_QUEUE_PUBLISH_TIMEOUT_SECONDS": "1",
            "TASK_QUEUE_MAINTENANCE_SECONDS": "1", "TASK_QUEUE_REDELIVERY_SECONDS": "1",
            "TASK_BEAT_STATUS_PATH": str(self.directory / "beat.json")}
        self.engine, self.sessions = database(dsn, self.schema)

        async def create():
            import agent_lab.models  # noqa: F401
            async with self.engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(text("""CREATE TABLE task_probe_events (
                    run_id uuid NOT NULL, attempt integer NOT NULL, process_id integer NOT NULL,
                    loop_id bigint NOT NULL, value integer NOT NULL,
                    started_at timestamptz NOT NULL, completed_at timestamptz, released_at timestamptz,
                    PRIMARY KEY (run_id, attempt))"""))
                await connection.execute(TaskPolicyRecord.__table__.insert().values(id=1,
                    policy=ExecutionPolicy(retry_delay_seconds=1).model_dump(), updated_at=datetime.now(UTC), updated_by="test"))
        self.run(create())

        async def publish(run_id, generation):
            await asyncio.to_thread(self.app.send_task, "agent_lab.execute", args=[run_id, generation], retry=False)
        self.service, self.worker, self.dispatcher, self.store = components(self.sessions, publish, owner="test-parent")
        self.start_redis()

    def start_redis(self):
        log = (self.directory / "redis.log").open("ab")
        self.logs.append(log)
        self.redis_process = subprocess.Popen([shutil.which("redis-server"), "--bind", "127.0.0.1",
            "--port", str(self.port), "--dir", str(self.directory), "--appendonly", "yes",
            "--appendfsync", "always", "--save", "", "--maxmemory-policy", "noeviction"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.wait(lambda: self.redis.ping(), timeout=10, transient=(redis.ConnectionError, redis.TimeoutError))

    def stop_redis(self):
        if self.redis_process is not None:
            self.stop(self.redis_process)
            self.redis_process = None
        self.redis.connection_pool.disconnect()

    def start_worker(self):
        return self.start("worker", "--pool=prefork", "--concurrency=2", "--hostname=queue-test@%h", "--without-gossip", "--without-mingle")

    def start_beat(self):
        process = self.start("beat", "--pidfile=")
        def ready():
            path = self.directory / "beat.json"
            if not path.exists():
                return False
            state = json.loads(path.read_text())
            return state["ready"] and state["pid"] == process.pid
        self.wait(ready, timeout=30)
        return process

    def start(self, role, *arguments):
        log = (self.directory / f"{role}-{len(self.children)}.log").open("ab")
        self.logs.append(log)
        child = subprocess.Popen([sys.executable, "-m", "celery", "-A", "tests.task_queue_app:app",
            role, "--loglevel=INFO", *arguments], env=self.env, cwd=Path(__file__).parents[1],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append(child)
        return child

    @staticmethod
    def stop(child, *, warm=True):
        if child.poll() is None:
            if warm:
                child.send_signal(signal.SIGTERM)  # 由 Celery 主进程停止领取并等待子进程工作。
            else:
                os.killpg(child.pid, signal.SIGKILL)
            try:
                child.wait(timeout=25 if warm else 5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        # 清理可以兜底强杀；正常关停的验收必须检查退出码，不能把超时当作成功。
        return child.returncode

    def wait(self, predicate, *, timeout=35, transient=()):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value:
                    return value
            except transient:
                pass
            time.sleep(0.1)
        pytest.fail(f"隔离队列验收超时；schema={self.schema}，日志目录={self.directory}", pytrace=False)

    def submit(self, **params):
        return self.run(self.service.submit("queue_probe", params, actor="test:queue", request_key=str(uuid4())))

    def detail(self, identity):
        return self.run(self.service.get_run(identity))

    def wait_status(self, identity, status, *, timeout=35):
        def current():
            value = self.detail(identity)
            return value if value.status == status else None
        return self.wait(current, timeout=timeout)

    def events(self, identity=None):
        return self.run(event_rows(self.sessions, identity))

    def release(self, identity):
        async def mark():
            async with self.sessions() as session:
                await session.execute(text("UPDATE task_probe_events SET released_at = now() WHERE run_id = :id"), {"id": identity})
                await session.commit()
        self.run(mark())

    def worker_log(self):
        return "\n".join(path.read_text() for path in self.directory.glob("worker-*.log"))

    def close(self):
        for child in reversed(self.children):
            self.stop(child)
        self.stop_redis()
        self.app.close()
        if self.engine is not None:
            async def drop():
                try:
                    async with self.engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
                finally:
                    await self.engine.dispose()
            self.run(drop())
        self.runner.close()
        for log in self.logs:
            log.close()


@pytest.fixture
def queue_environment(tmp_path):
    environment = QueueEnvironment(tmp_path)
    try:
        environment.open()
        yield environment
    finally:
        environment.close()


def test_prefork_reuses_one_loop_per_child_and_ignores_duplicate_delivery(queue_environment):
    env = queue_environment
    env.start_worker()
    beat = env.start_beat()
    receipts = [env.submit(value=value, seconds=0.5) for value in range(8)]
    duplicates = []
    for receipt in receipts:
        for _ in range(2):
            duplicates.append(env.app.send_task("agent_lab.execute", args=[str(receipt.run_id), 1]).id)
    details = [env.wait_status(item.run_id, "succeeded") for item in receipts]
    # 终态只说明原消息完成；还要等后排重复消息确实处理完再断言业务没有重做。
    env.wait(lambda: all(f"queue_probe_processed task_id={identity}" in env.worker_log() for identity in duplicates))
    events = env.events()
    assert len(events) == 8 and all(item.attempts == 1 for item in details)
    assert {item.stats["value"] for item in details} == set(range(8))
    processes = {item["process_id"] for item in events}
    assert len(processes) == 2
    for pid in processes:
        handled = [item for item in events if item["process_id"] == pid]
        assert len(handled) >= 2 and len({item["loop_id"] for item in handled}) == 1
    assert env.stop(beat) == 0
    assert json.loads((env.directory / "beat.json").read_text())["ready"] is False


def test_publish_failure_lost_message_and_redis_aof_restart_preserve_receipts(queue_environment):
    env = queue_environment
    env.stop_redis()
    pending = env.submit(value=21)
    assert env.detail(pending.run_id).status == "queued"
    assert env.detail(pending.run_id).dispatch_error_type
    env.start_redis()
    def published():
        env.run(env.dispatcher.publish_due())
        return env.redis.llen(env.schema + ":" + env.queue) > 0
    env.wait(published)
    env.stop_redis()
    env.start_redis()
    assert env.redis.llen(env.schema + ":" + env.queue) > 0  # AOF 中的消息仍在。
    env.redis.delete(env.schema + ":" + env.queue)  # 只丢弃本测试专用队列消息。
    env.start_worker()
    env.start_beat()
    completed = env.wait_status(pending.run_id, "succeeded")
    assert completed.attempts == 1 and len(env.events(pending.run_id)) == 1

    # 保持同一 Worker 主进程，验证它跨 Redis 断线重连后还能完成原任务和领取新任务。
    working = env.submit(wait_for_release=True)
    env.wait(lambda: env.events(working.run_id))
    env.stop_redis()
    env.start_redis()
    env.release(working.run_id)
    assert env.wait_status(working.run_id, "succeeded").attempts == 1
    after_restart = env.submit(value=22)
    assert env.wait_status(after_restart.run_id, "succeeded").stats["value"] == 22
    assert len(env.events(working.run_id)) == 1


def test_visibility_redelivery_and_worker_warm_restart_do_not_restart_business(queue_environment):
    env = queue_environment
    worker = env.start_worker()
    env.start_beat()
    long = env.submit(wait_for_release=True)
    env.wait(lambda: env.events(long.run_id))
    # Kombu 默认实际恢复扫描约百秒一次；不改生产轮询，只缩短可见性并等待真实证据。
    env.wait(lambda: f"queue_probe_delivery run_id={long.run_id} redelivered=True" in env.worker_log(), timeout=130)
    env.release(long.run_id)
    env.wait_status(long.run_id, "succeeded")
    assert len(env.events(long.run_id)) == 1
    warm = env.submit(seconds=2)
    env.wait(lambda: env.events(warm.run_id))
    assert env.stop(worker) == 0
    assert env.detail(warm.run_id).status == "succeeded"
    accepted = env.submit(value=99)
    env.start_worker()
    assert env.wait_status(accepted.run_id, "succeeded").stats["value"] == 99


@pytest.mark.parametrize("completed", [False, True])
def test_forced_worker_loss_uses_business_evidence_or_requires_verification(queue_environment, completed):
    env = queue_environment
    worker = env.start_worker()
    env.start_beat()
    receipt = env.submit(seconds=0.1 if completed else 120, after_completion_seconds=120 if completed else 0)
    def evidence():
        rows = env.events(receipt.run_id)
        return rows[0] if rows and (not completed or rows[0]["completed_at"] is not None) else None
    row = env.wait(evidence)
    # PID 来自本随机 schema，并核实仍属于本次 Worker 进程组，避免误伤其他进程。
    assert os.getpgid(row["process_id"]) == worker.pid
    os.kill(row["process_id"], signal.SIGKILL)
    final = env.wait_status(receipt.run_id, "succeeded" if completed else "needs_attention", timeout=65)
    assert final.attempts == 1 and len(env.events(receipt.run_id)) == 1
    assert final.claim_token is None


def test_dynamic_beat_accepts_future_event_once_with_latest_configuration(queue_environment):
    env = queue_environment
    env.start_worker()
    env.start_beat()
    async def create():
        async with env.sessions() as session:
            manager = ScheduledJobService(session, CronSchedule(), env.service.registry)
            view = await manager.create_job(key="dynamic-probe", task_type="queue_probe", cron_expr="* * * * *", params={"value": 13}, enabled=True)
            return view.record.id
    job_id = env.run(create())
    async def next_event():
        async with env.sessions() as session:
            # 缩短等待，但仍让真实 Beat 按生产版本复核/受理/推进路径处理未来计划。
            await session.execute(update(ScheduledJobRecord).where(ScheduledJobRecord.id == job_id)
                .values(next_run_at=datetime.now(UTC) + timedelta(seconds=2)))
            await session.commit()
    env.run(next_event())
    def accepted(value):
        async def find():
            async with env.sessions() as session:
                return await session.scalar(select(JobRunRecord).where(JobRunRecord.source_job_id == job_id,
                    JobRunRecord.status == "succeeded", JobRunRecord.stats["value"].as_integer() == value))
        return env.run(find())
    first = env.wait(lambda: accepted(13))
    assert first.stats["value"] == 13
    async def change():
        async with env.sessions() as session:
            manager = ScheduledJobService(session, CronSchedule(), env.service.registry)
            await manager.update_job(job_id, params={"value": 27})
    env.run(change())
    env.run(next_event())
    second = env.wait(lambda: accepted(27))
    assert second.id != first.id and second.config_snapshot["params"]["value"] == 27
    async def delete():
        async with env.sessions() as session:
            manager = ScheduledJobService(session, CronSchedule(), env.service.registry)
            await manager.delete_job(job_id)
    env.run(delete())
    retained = env.detail(first.id)
    assert retained.job_id is None and retained.source_job_id == job_id
    assert retained.config_snapshot["params"]["value"] == 13
    assert env.detail(second.id).source_job_id == job_id


def test_database_automatic_retry_has_one_identity_and_new_attempts(queue_environment):
    env = queue_environment
    env.start_worker()
    env.start_beat()
    receipt = env.submit(failures=2)
    result = env.wait_status(receipt.run_id, "succeeded")
    events = sorted(env.events(receipt.run_id), key=lambda row: row["attempt"])
    assert result.attempts == 3 and [item["attempt"] for item in events] == [1, 2, 3]
    assert events[1]["started_at"] - events[0]["started_at"] >= timedelta(seconds=1)
    assert events[2]["started_at"] - events[1]["started_at"] >= timedelta(seconds=2)
