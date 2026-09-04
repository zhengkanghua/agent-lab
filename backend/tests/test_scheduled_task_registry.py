"""定时任务类型注册表与调度器 cron 工具的完全离线测试。

覆盖三件事：参数模型按类型收敛与拒绝未知字段；未知任务类型识别；调度器包装器的
cron 解析与未来执行时间预览。不访问 PostgreSQL、APScheduler 不真正到点触发。
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.services.scheduled_task_registry import (
    TASK_TYPE_SPECS,
    FreshRssSyncTaskParams,
    IndexPendingTaskParams,
    PruneOldDocumentsTaskParams,
    get_task_type_spec,
)
from agent_lab.services.scheduler_runner import ScheduledJobRunner


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


class TestTaskTypeRegistry:
    def test_registry_has_exactly_the_three_v1_types(self) -> None:
        # 类型清单是代码契约：多一个没实现的类型会让管理端出现「选了就执行失败」的选项，
        # 少一个则种子任务无法加载。这条测试就是注册表的形状锁。
        assert set(TASK_TYPE_SPECS) == {"freshrss_sync", "index_pending", "prune_old_documents"}

    def test_get_task_type_spec_returns_none_for_unknown(self) -> None:
        assert get_task_type_spec("no_such_type") is None

    def test_freshrss_sync_params_defaults_and_bounds(self) -> None:
        params = FreshRssSyncTaskParams.model_validate({})
        assert params.limit_per_source == 2
        with pytest.raises(ValidationError):
            FreshRssSyncTaskParams.model_validate({"limit_per_source": 0})
        with pytest.raises(ValidationError):
            FreshRssSyncTaskParams.model_validate({"limit_per_source": 101})
        with pytest.raises(ValidationError):
            # 未知字段必须拒绝：静默吞掉会让调用方以为参数生效了。
            FreshRssSyncTaskParams.model_validate({"batch_size": 5})

    def test_index_pending_params_defaults_and_bounds(self) -> None:
        params = IndexPendingTaskParams.model_validate({})
        assert params.batch_size == 20
        assert params.stale_after_minutes == 60
        with pytest.raises(ValidationError):
            IndexPendingTaskParams.model_validate({"batch_size": 0})
        with pytest.raises(ValidationError):
            IndexPendingTaskParams.model_validate({"stale_after_minutes": 0})

    def test_prune_old_documents_params_defaults_and_bounds(self) -> None:
        params = PruneOldDocumentsTaskParams.model_validate({})
        assert params.retention_days == 180
        assert params.dry_run is True  # 默认预演模式
        with pytest.raises(ValidationError):
            PruneOldDocumentsTaskParams.model_validate({"retention_days": 29})
        with pytest.raises(ValidationError):
            PruneOldDocumentsTaskParams.model_validate({"retention_days": 731})
        # 边界值应该合法
        PruneOldDocumentsTaskParams.model_validate({"retention_days": 30})
        PruneOldDocumentsTaskParams.model_validate({"retention_days": 730})

    def test_validate_params_fills_defaults_and_rejects_unknowns(self) -> None:
        spec = get_task_type_spec("index_pending")
        assert spec is not None
        # 缺省字段补默认值；未知字段必须拒绝——静默吞掉会让调用方以为参数生效了。
        normalized = spec.validate_params({"batch_size": 5})
        assert normalized == {"batch_size": 5, "stale_after_minutes": 60}
        with pytest.raises(ValidationError):
            spec.validate_params({"batch_size": 5, "junk": "x"})


class TestCronUtilities:
    def _runner(self) -> ScheduledJobRunner:
        settings = SchedulerSettings(timezone="Asia/Shanghai")
        return ScheduledJobRunner(
            store_factory=lambda: None,  # type: ignore[arg-type,return-value]
            write_runtime_factory=lambda: None,  # type: ignore[arg-type,return-value]
            settings=settings,
            clock=lambda: datetime(2026, 9, 2, 4, 0, tzinfo=UTC),  # 北京时间 12:00
        )

    def test_next_run_at_is_none_before_start(self) -> None:
        # 调度器未启动（SCHEDULER_ENABLED 关闭）时没有「下次执行时间」可言，
        # 管理 API 必须拿到 None 而不是抛异常。
        runner = self._runner()
        from uuid import uuid4

        assert runner.next_run_at(uuid4()) is None

    def test_upcoming_fire_times_interprets_cron_in_configured_timezone(self) -> None:
        runner = self._runner()
        utc_times, local_times = runner.upcoming_fire_times("0 9 * * *")
        assert len(utc_times) == 3
        # 北京早上 9 点 = UTC 前一天 01:00；注入时钟是北京时间 9/2 12:00，
        # 所以第一次触发是 9/3 09:00 北京 = 9/3 01:00 UTC。
        assert utc_times[0] == datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
        assert local_times[0].startswith("2026-09-03T09:00:00+08:00")
        # 三次执行时间严格递增且间隔 24 小时。
        assert utc_times[1] == datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
        assert utc_times[2] == datetime(2026, 9, 5, 1, 0, tzinfo=UTC)

    def test_upcoming_fire_times_supports_step_expressions(self) -> None:
        # 注入时钟落在 04:03，避开整点边界：*/10 的下一次触发是 04:10 而不是当前时刻。
        runner = ScheduledJobRunner(
            store_factory=lambda: None,  # type: ignore[arg-type,return-value]
            write_runtime_factory=lambda: None,  # type: ignore[arg-type,return-value]
            settings=SchedulerSettings(timezone="Asia/Shanghai"),
            clock=lambda: datetime(2026, 9, 2, 4, 3, tzinfo=UTC),
        )
        utc_times, _ = runner.upcoming_fire_times("*/10 * * * *")
        assert len(utc_times) == 3
        assert utc_times[0] == datetime(2026, 9, 2, 4, 10, tzinfo=UTC)
        assert utc_times[1] == datetime(2026, 9, 2, 4, 20, tzinfo=UTC)

    def test_parse_cron_rejects_invalid_expressions(self) -> None:
        runner = self._runner()
        with pytest.raises(ValueError):
            runner.parse_cron("not a cron")
        with pytest.raises(ValueError):
            runner.parse_cron("99 * * * *")
