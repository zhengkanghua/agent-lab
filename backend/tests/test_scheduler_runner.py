"""定时任务调度器包装器的行为测试：假 Store + 假写 Runtime，完全离线。

覆盖统一执行包装器的外部可见行为：两种任务类型只跑各自的步骤、Runtime 用完即关、
失败记 error_type、重叠触发记 skipped、历史裁剪按保留条数执行。cron 到点入口
（``_run_scheduled``）就是 APScheduler 的回调签名，直接调用它等于模拟一次到点触发。
不访问 PostgreSQL、FreshRSS、Ollama 或 Qdrant，APScheduler 只做注册断言、不等真实到点。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.services.scheduled_task_errors import ScheduledJobAlreadyRunningError
from agent_lab.services.scheduler_runner import SKIPPED_PREVIOUS_RUNNING_REASON, ScheduledJobRunner


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


def make_job(
    *,
    task_type: str = "freshrss_sync",
    params: dict | None = None,
    enabled: bool = True,
) -> ScheduledJobRecord:
    """构造一条不落库的定时任务记录。"""

    return ScheduledJobRecord(
        id=uuid4(),
        key="test-job",
        task_type=task_type,
        cron_expr="*/10 * * * *",
        params=params or {"limit_per_source": 2},
        enabled=enabled,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class FakeStore:
    """在内存里记录执行历史的 Store 替身，语义与 ScheduledJobStore 对齐。"""

    def __init__(self) -> None:
        self.jobs: dict[UUID, ScheduledJobRecord] = {}
        self.runs: list[SimpleNamespace] = []
        self.prunes: list[tuple[UUID, int]] = []

    async def load_enabled_jobs(self) -> list[ScheduledJobRecord]:
        return [job for job in self.jobs.values() if job.enabled]

    async def get_job(self, job_id: UUID) -> ScheduledJobRecord | None:
        return self.jobs.get(job_id)

    async def record_skipped(
        self, job_id: UUID, *, trigger_type: str, started_at: datetime, stats: dict
    ) -> UUID:
        record = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            trigger_type=trigger_type,
            status="skipped",
            started_at=started_at,
            finished_at=None,
            stats=stats,
            error_type=None,
        )
        self.runs.append(record)
        return record.id

    async def start_run(
        self, job_id: UUID, *, trigger_type: str, started_at: datetime
    ) -> UUID:
        record = SimpleNamespace(
            id=uuid4(),
            job_id=job_id,
            trigger_type=trigger_type,
            status="running",
            started_at=started_at,
            finished_at=None,
            stats={},
            error_type=None,
        )
        self.runs.append(record)
        return record.id

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        finished_at: datetime | None,
        stats: dict,
        error_type: str | None,
    ) -> None:
        for record in self.runs:
            if record.id == run_id:
                record.status = status
                record.finished_at = finished_at
                record.stats = stats
                record.error_type = error_type
                return

    async def prune_runs(self, job_id: UUID, *, keep: int) -> None:
        self.prunes.append((job_id, keep))

    def find(self, run_id: UUID) -> SimpleNamespace | None:
        for record in self.runs:
            if record.id == run_id:
                return record
        return None


class FakeWriteRuntime:
    """记录调用并可控失败/阻塞的写 Runtime 替身。"""

    def __init__(
        self,
        *,
        sync_error: Exception | None = None,
        index_error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.sync_calls: list[dict] = []
        self.index_calls: list[dict] = []
        self.closed = False
        self._sync_error = sync_error
        self._index_error = index_error
        self._gate = gate

    async def sync_only(self, *, limit_per_source: int) -> Any:
        self.sync_calls.append({"limit_per_source": limit_per_source})
        if self._gate is not None:
            await self._gate.wait()
        if self._sync_error is not None:
            raise self._sync_error
        return SimpleNamespace(
            source_count=2,
            successful_source_count=1,
            synchronized_count=3,
            checkpoint_advanced_count=1,
            failed_source_count=1,
            failures=(SimpleNamespace(error_type="FreshRSSConnectionError"),),
        )

    async def index_only(self, *, batch_size: int, stale_after: Any) -> Any:
        self.index_calls.append({"batch_size": batch_size, "stale_after": stale_after})
        if self._index_error is not None:
            raise self._index_error
        return SimpleNamespace(
            requeued_stale_count=1,
            candidate_count=3,
            indexed_count=2,
            skipped_count=0,
            failed_count=0,
            failures=(),
        )

    async def close(self) -> None:
        self.closed = True


def make_runner(
    store: FakeStore,
    runtime: FakeWriteRuntime,
    *,
    settings: SchedulerSettings | None = None,
    clock: Any | None = None,
) -> ScheduledJobRunner:
    """组装被测调度器：默认固定时钟，Store/Runtime 都是替身。"""

    return ScheduledJobRunner(
        store_factory=lambda: store,
        write_runtime_factory=lambda: runtime,
        settings=settings or SchedulerSettings(timezone="Asia/Shanghai"),
        clock=clock or (lambda: datetime(2026, 9, 2, 4, 0, tzinfo=UTC)),
    )


async def wait_for_terminal(store: FakeStore, run_id: UUID, *, timeout: float = 2.0) -> SimpleNamespace:
    """轮询直到该执行记录离开 running 状态，避免测试挂死。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        record = store.find(run_id)
        if record is not None and record.status != "running":
            return record
        await asyncio.sleep(0.01)
    raise AssertionError("执行记录在超时内未进入终态")


class TestUnifiedExecution:
    def test_manual_trigger_runs_only_sync_step_and_closes_runtime(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync", params={"limit_per_source": 7})
        store.jobs[job.id] = job

        async def scenario() -> None:
            run_id = await runner.trigger_now(job)
            assert isinstance(run_id, UUID)
            record = await wait_for_terminal(store, run_id)
            assert record.status == "succeeded"
            assert record.trigger_type == "manual"
            assert record.error_type is None
            assert record.stats["synchronized_document_count"] == 3
            assert record.stats["failures"] == {"FreshRSSConnectionError": 1}
            # 只跑了同步这一步，绝不碰索引；Runtime 用完即关。
            assert runtime.sync_calls == [{"limit_per_source": 7}]
            assert runtime.index_calls == []
            assert runtime.closed is True

        run(scenario())

    def test_scheduled_index_task_runs_only_index_step(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        job = make_job(
            task_type="index_pending",
            params={"batch_size": 5, "stale_after_minutes": 30},
        )
        store.jobs[job.id] = job

        async def scenario() -> None:
            run_id = await store.start_run(
                job.id, trigger_type="scheduled", started_at=datetime.now(UTC)
            )
            await runner._execute(job, run_id, "scheduled")
            record = store.find(run_id)
            assert record is not None and record.status == "succeeded"
            assert record.stats["candidate_count"] == 3
            assert record.stats["requeued_stale_count"] == 1
            assert runtime.index_calls == [
                {"batch_size": 5, "stale_after": timedelta(minutes=30)}
            ]
            assert runtime.sync_calls == []
            assert runtime.closed is True

        run(scenario())

    def test_failure_records_error_type_and_closes_runtime(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime(
            sync_error=RuntimeError("boom")
        )
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            run_id = await runner.trigger_now(job)
            record = await wait_for_terminal(store, run_id)
            assert record.status == "failed"
            # 历史只记异常类名，不记异常文本（"boom" 不得出现）。
            assert record.error_type == "RuntimeError"
            assert record.stats == {}
            assert runtime.closed is True

        run(scenario())

    def test_unknown_task_type_fails_with_its_class_name(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        job = make_job(task_type="no_such_type")
        store.jobs[job.id] = job

        async def scenario() -> None:
            run_id = await runner.trigger_now(job)
            record = await wait_for_terminal(store, run_id)
            assert record.status == "failed"
            assert record.error_type == "ScheduledJobUnknownTypeError"
            # 未执行任何业务步骤。
            assert runtime.sync_calls == []
            assert runtime.index_calls == []

        run(scenario())

    def test_history_pruned_with_configured_retention(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(
            store, runtime, settings=SchedulerSettings(run_history_retention=3)
        )
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            run_id = await runner.trigger_now(job)
            await wait_for_terminal(store, run_id)
            assert store.prunes == [(job.id, 3)]

        run(scenario())


class TestOverlapPolicy:
    def test_cron_firing_while_running_records_skipped(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        gate = asyncio.Event()
        runtime._gate = gate
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            # 1、先起一轮手动执行，靠 gate 卡在同步步骤里（running 态）。
            running_id = await runner.trigger_now(job)
            await asyncio.sleep(0.05)
            # 2、模拟 cron 到点：上一轮还没结束，必须记 skipped 且不起第三步。
            await runner._run_scheduled(job.id)
            gate.set()
            await wait_for_terminal(store, running_id)

            skipped = [r for r in store.runs if r.status == "skipped"]
            assert len(skipped) == 1
            assert skipped[0].trigger_type == "scheduled"
            assert skipped[0].stats == {"reason": SKIPPED_PREVIOUS_RUNNING_REASON}
            # 跳过的那轮没有真正执行业务步骤（gate 放行前只有一次 sync 调用）。
            assert len(runtime.sync_calls) == 1

        run(scenario())

    def test_manual_trigger_conflict_raises_already_running(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        gate = asyncio.Event()
        runtime._gate = gate
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            first = await runner.trigger_now(job)
            await asyncio.sleep(0.05)
            with pytest.raises(ScheduledJobAlreadyRunningError):
                await runner.trigger_now(job)
            gate.set()
            await wait_for_terminal(store, first)

        run(scenario())


class TestSchedulerLifecycle:
    def test_start_loads_enabled_jobs_and_registers_them(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        enabled = make_job(task_type="freshrss_sync")
        disabled = make_job(task_type="index_pending", params={}, enabled=False)
        store.jobs[enabled.id] = enabled
        store.jobs[disabled.id] = disabled

        async def scenario() -> None:
            await runner.start()
            try:
                # 只有启用的任务被注册；下次执行时间可查询。
                assert runner.next_run_at(enabled.id) is not None
                assert runner.next_run_at(disabled.id) is None
            finally:
                await runner.close()
            # 关闭后不再有下次执行时间。
            assert runner.next_run_at(enabled.id) is None

        run(scenario())

    def test_apply_job_removes_entry_when_disabled(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            await runner.start()
            try:
                assert runner.next_run_at(job.id) is not None
                job.enabled = False
                runner.apply_job(job)
                assert runner.next_run_at(job.id) is None
            finally:
                await runner.close()

        run(scenario())

    def test_cron_firing_skips_when_job_disabled_in_db(self) -> None:
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        job = make_job(task_type="freshrss_sync")
        store.jobs[job.id] = job

        async def scenario() -> None:
            # 注册后配置被改停用：到点回调重读数据库后必须安静放弃，不起执行。
            await runner.start()
            try:
                job.enabled = False
                await runner._run_scheduled(job.id)
                assert store.runs == []
                assert runtime.sync_calls == []
            finally:
                await runner.close()

        run(scenario())
