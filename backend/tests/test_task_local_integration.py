"""使用已有 Redis/PostgreSQL 的本地 solo 联调；不依赖 Docker，不控制共享服务。

只创建随机 schema、任务键和本测试的 API/Beat/Worker。生产清理仅预演空知识库，
不连接 FreshRSS、S3、Embedding 或 Qdrant；真实 prefork 故障验收另见 Linux 夹具。
"""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

from celery import Celery
from fastapi_users.password import PasswordHelper
import httpx
import pytest
from redis import Redis
from sqlalchemy import text

from agent_lab.config.redis import get_redis_settings
from agent_lab.config.settings import get_settings
from agent_lab.db.base import Base
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.scheduled_job import TaskPolicyRecord
from agent_lab.models.user import UserRecord
from agent_lab.tasks.contracts import ExecutionPolicy
from agent_lab.tasks.status import process_exists
from tests.task_queue_support import database

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TASK_LOCAL_INTEGRATION_TEST") != "1",
    reason="需授权使用已配置 Redis/PostgreSQL 的随机资源和本地进程进行联调。",
)


class LocalTaskEnvironment:
    """独立于故障夹具，根本不提供重启 Redis 或 FLUSHDB 的操作。"""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.schema = f"task_queue_test_{uuid4().hex}"
        self.prefix = f"tasks:{self.schema}:"
        self.worker_name = f"local-task@{uuid4().hex}"
        self.knowledge_base_id = uuid4()
        self.email = f"local-task-{uuid4().hex}@example.com"
        self.password = secrets.token_urlsafe(24)
        self.children, self.logs = [], []
        self.worker = self.engine = self.client = None
        self.redis_ready = False
        self.report = {"schema": self.schema, "checks": [], "redis_keys_cleaned": False, "schema_cleaned": False}
        self.runner = asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
        settings = get_redis_settings()
        url, password = settings.url.get_secret_value(), settings.password.get_secret_value()
        self.redis = Redis.from_url(url, password=password or None, socket_timeout=5, socket_connect_timeout=5)
        self.sender = Celery("task_local_sender", broker=url)
        self.sender.conf.update(task_default_queue=self.schema, task_ignore_result=True,
            broker_password=password or None, broker_transport_options={
                "global_keyprefix": self.prefix, "socket_timeout": 5, "socket_connect_timeout": 5,
            })
        self.env = {**os.environ, "TASK_TEST_SCHEMA": self.schema,
            "TASK_TEST_DATABASE_URL": str(get_settings().database_url),
            "REDIS_URL": url, "REDIS_PASSWORD": password, "TASK_QUEUE_NAME": self.schema,
            "TASK_QUEUE_PUBLISH_TIMEOUT_SECONDS": "5", "TASK_QUEUE_REDELIVERY_SECONDS": "1",
            "TASK_QUEUE_MAINTENANCE_SECONDS": "1", "TASK_BEAT_STATUS_PATH": str(self.directory / "beat.json"),
            "AUTH_COOKIE_SECURE": "false", "AUTH_COOKIE_NAME": "task_local_auth",
            "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}

    def open(self):
        self.redis.ping()
        self.redis_ready = True
        self.engine, self.sessions = database(self.env["TASK_TEST_DATABASE_URL"], self.schema)

        async def prepare():
            import agent_lab.models  # noqa: F401

            async with self.engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
                await connection.run_sync(Base.metadata.create_all)
            async with self.sessions() as session:
                session.add(KnowledgeBaseRecord(id=self.knowledge_base_id, key="task-local", name="任务联调空知识库"))
                session.add(UserRecord(id=uuid4(), email=self.email, hashed_password=PasswordHelper().hash(self.password),
                    is_active=True, is_superuser=True, is_verified=True))
                session.add(TaskPolicyRecord(id=1, policy=ExecutionPolicy().model_dump(),
                    updated_at=datetime.now(UTC), updated_by="local-test"))
                await session.commit()
        self.runner.run(prepare())
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30, trust_env=False)
        self.start("api", "uvicorn", "tests.task_local_app:create_api", "--factory", "--host", "127.0.0.1",
            "--port", str(self.port), "--loop", "agent_lab.runtime:selector_loop_factory")
        self.wait(lambda: self.client.get("/auth/me").status_code == 401, transient=(httpx.TransportError,))

    def start(self, role, module, *arguments):
        log = (self.directory / f"{role}.log").open("ab")
        self.logs.append(log)
        child = subprocess.Popen([sys.executable, "-m", module, *arguments], env=self.env,
            cwd=Path(__file__).parents[1], stdout=log, stderr=subprocess.STDOUT)
        self.children.append(child)
        return child

    def start_worker(self):
        self.worker = self.start("worker", "celery", "-A", "tests.task_local_app:app", "worker",
            "--pool=solo", "--concurrency=1", f"--hostname={self.worker_name}", "--loglevel=INFO",
            "--without-gossip", "--without-mingle")
        self.wait(lambda: self.sender.control.inspect(destination=[self.worker_name], timeout=2).ping())

    def start_beat(self):
        child = self.start("beat", "celery", "-A", "tests.task_local_app:app", "beat", "--pidfile=", "--loglevel=INFO")
        def ready():
            path = self.directory / "beat.json"
            if not path.exists():
                return False
            value = json.loads(path.read_text())
            # Windows 虚拟环境启动器与实际 Python 的 PID 不同；文件路径已按本次夹具隔离。
            return value["ready"] and child.poll() is None and process_exists(value["pid"])
        self.wait(ready)

    def wait(self, predicate, *, timeout=90, transient=()):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except transient:
                pass
            if any(child.poll() is not None for child in self.children):
                pytest.fail(f"联调子进程提前退出，日志目录={self.directory}", pytrace=False)
            time.sleep(0.2)
        pytest.fail(f"本地联调超时，日志目录={self.directory}", pytrace=False)

    def login(self):
        response = self.client.post("/auth/login", data={"username": self.email, "password": self.password})
        assert response.status_code == 204
        assert self.client.get("/auth/me").json()["email"] == self.email

    def submit(self, key):
        response = self.client.post("/task-runs", headers={"Idempotency-Key": key}, json={
            "task_type": "prune_old_documents", "params": {"retention_days": 180, "dry_run": True,
                "knowledge_base_ids": [str(self.knowledge_base_id)]},
        })
        assert response.status_code == 202
        return response.json()

    def detail(self, identity):
        response = self.client.get(f"/task-runs/{identity}")
        assert response.status_code == 200
        return response.json()

    def wait_success(self, identity):
        def complete():
            value = self.detail(identity)
            assert value["status"] not in {"failed", "needs_attention"}, value["error_type"]
            return value if value["status"] == "succeeded" else None
        return self.wait(complete)

    def worker_log(self):
        return (self.directory / "worker.log").read_text(encoding="utf-8", errors="replace")

    def stop_worker(self):
        if self.worker is not None and self.worker.poll() is None:
            # 精确指定本测试 Worker；其控制频道也由随机前缀隔离。
            self.sender.control.broadcast("shutdown", destination=[self.worker_name])
            self.worker.wait(timeout=30)

    def close(self):
        # 仅回收自己创建的进程。共享 Redis/PostgreSQL 不停止、不清空。
        for child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self.sender.close()
        if self.client is not None:
            self.client.close()
        try:
            if self.redis_ready:
                keys = list(self.redis.scan_iter(match=self.prefix + "*"))
                if keys:
                    self.redis.delete(*keys)
                self.report["redis_keys_cleaned"] = not any(self.redis.scan_iter(match=self.prefix + "*"))
        finally:
            try:
                if self.engine is not None:
                    async def drop():
                        try:
                            async with self.engine.begin() as connection:
                                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
                            self.report["schema_cleaned"] = True
                        finally:
                            await self.engine.dispose()
                    self.runner.run(drop())
            finally:
                self.runner.close()
                self.redis.close()
                for log in self.logs:
                    log.close()
                (self.directory / "report.json").write_text(json.dumps(self.report, indent=2), encoding="utf-8")


@pytest.fixture
def local_environment(tmp_path):
    environment = LocalTaskEnvironment(tmp_path)
    try:
        environment.open()
        yield environment
    finally:
        environment.close()


def test_http_beat_and_solo_worker_with_shared_redis(local_environment):
    env = local_environment
    env.login()
    env.report["checks"].append("real_login_and_cookie")
    cancelled = env.submit("cancelled")
    assert env.submit("cancelled")["run_id"] == cancelled["run_id"]
    assert env.client.post(f"/task-runs/{cancelled['run_id']}/cancel").json()["status"] == "cancelled"
    accepted = env.submit("lost-message")
    # 只丢弃本测试队列中的消息，数据库中的受理记录必须由真实 Beat 补投。
    queue_key = env.prefix + env.schema
    assert env.redis.llen(queue_key) > 0
    env.redis.delete(queue_key)
    env.start_worker()
    assert env.detail(accepted["run_id"])["status"] == "queued"
    env.start_beat()
    completed = env.wait_success(accepted["run_id"])
    assert completed["attempts"] == 1
    assert completed["stats"]["dry_run"] is True and completed["stats"]["documents_deleted"] == 0
    assert env.submit("lost-message")["run_id"] == accepted["run_id"]
    env.report["checks"].append("beat_redelivers_lost_message_to_production_handler")

    duplicate = env.sender.send_task("agent_lab.execute", args=[cancelled["run_id"], 1], retry=False)
    env.wait(lambda: f"task_local_processed task_id={duplicate.id}" in env.worker_log())
    detail = env.detail(cancelled["run_id"])
    assert detail["status"] == "cancelled" and detail["attempts"] == 0
    env.report["checks"].append("cancelled_delivery_cannot_start_business")

    another = env.submit("next-execution")
    assert env.wait_success(another["run_id"])["attempts"] == 1
    env.stop_worker()
    assert env.worker.returncode == 0
    assert "task_local_runtime_closed" in env.worker_log()
    env.report["checks"].extend(["successive_solo_tasks", "warm_worker_shutdown_closes_runtime"])
