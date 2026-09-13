"""定时任务类型注册表与调度器 cron 工具的完全离线测试。

覆盖三件事：参数模型按类型收敛与拒绝未知字段；未知任务类型识别；调度器包装器的
cron 解析与未来执行时间预览。不访问 PostgreSQL、APScheduler 不真正到点触发。
"""

import asyncio
import json
from datetime import UTC, datetime
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError
from psycopg.types.json import Jsonb, JsonbDumper
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.services.scheduled_task_registry import (
    TASK_TYPE_SPECS,
    FreshRssSyncTaskParams,
    IndexPendingTaskParams,
    PruneOldDocumentsTaskParams,
    get_task_type_spec,
)
from agent_lab.tasks.cron import CronSchedule
from tests.task_helpers import task_system, execute_accepted


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


class TestTaskTypeRegistry:
    def test_schedulable_and_direct_types_have_implemented_handlers(self) -> None:
        assert {key for key, spec in TASK_TYPE_SPECS.items() if spec.schedulable} == {
            "freshrss_sync", "index_pending", "prune_old_documents",
        }
        assert all(callable(spec.execute) for spec in TASK_TYPE_SPECS.values())
        assert not TASK_TYPE_SPECS["pipeline_run_once"].schedulable
        assert not TASK_TYPE_SPECS["document_processing"].schedulable

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
        # 缺省范围只解析到新闻库，兼容旧任务配置且绝不解释为全库清理。
        assert params.knowledge_base_ids == [DEFAULT_NEWS_KNOWLEDGE_BASE_ID]
        with pytest.raises(ValidationError):
            PruneOldDocumentsTaskParams.model_validate({"retention_days": 29})
        with pytest.raises(ValidationError):
            PruneOldDocumentsTaskParams.model_validate({"retention_days": 731})
        # 空范围无法表达保留策略；重复 ID 去重后保留首次出现顺序。
        with pytest.raises(ValidationError):
            PruneOldDocumentsTaskParams.model_validate({"knowledge_base_ids": []})
        scoped = PruneOldDocumentsTaskParams.model_validate({
            "knowledge_base_ids": [
                "10000000-0000-4000-8000-000000000011",
                str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID),
                "10000000-0000-4000-8000-000000000011",
            ]
        })
        assert scoped.knowledge_base_ids == [
            UUID("10000000-0000-4000-8000-000000000011"),
            DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
        ]
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

    @pytest.mark.parametrize("raw", [
        None,
        {"knowledge_base_ids": [str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)]},
        {"knowledge_base_ids": [
            "10000000-0000-4000-8000-000000000011",
            str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID),
            "10000000-0000-4000-8000-000000000011",
        ]},
    ])
    def test_prune_config_and_run_snapshot_are_jsonb_serializable(self, raw) -> None:
        spec = get_task_type_spec("prune_old_documents")
        assert spec is not None
        params = spec.validate_params(raw)
        dialect = PGDialect_psycopg()
        binder = JSONB().dialect_impl(dialect).bind_processor(dialect)
        assert binder is not None
        for value in (params, {"task_type": spec.task_type, "params": params}):
            encoded = JsonbDumper(Jsonb).dump(binder(value))
            assert json.loads(encoded) == value
        assert len(params["knowledge_base_ids"]) == len(set(params["knowledge_base_ids"]))

    @pytest.mark.parametrize("raw", [{}, {"knowledge_base_ids": [str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)]}])
    def test_executor_restores_uuid_scope_from_json_configuration(self, raw) -> None:
        async def verify() -> None:
            runtime = SimpleNamespace(
                prune_old_documents=AsyncMock(return_value=SimpleNamespace(to_job_run_stats=lambda: {})),
                close=AsyncMock(),
            )
            spec = replace(TASK_TYPE_SPECS["prune_old_documents"], runtime_factory=lambda: runtime,
                           execution_scope=lambda *_: nullcontext())
            async with task_system(spec) as system:
                accepted = await system.service.submit(spec.task_type, raw, actor="user:1", request_key="a")
                result = await execute_accepted(system, accepted)
                runtime.prune_old_documents.assert_awaited_once_with(
                    retention_days=180, dry_run=True, knowledge_base_ids=[DEFAULT_NEWS_KNOWLEDGE_BASE_ID],
                )
                assert result.status == "succeeded"
                runtime.close.assert_awaited_once()

        run(verify())


class TestCronUtilities:
    def _runner(self) -> CronSchedule:
        return CronSchedule("Asia/Shanghai", clock=lambda: datetime(2026, 9, 2, 4, 0, tzinfo=UTC))

    def test_upcoming_fire_times_interprets_cron_in_configured_timezone(self) -> None:
        runner = self._runner()
        utc_times, local_times = runner.upcoming_fire_times("0 9 * * *")
        assert len(utc_times) == 3
        # 北京早上 9 点 = UTC 同一天 01:00；注入时钟是北京时间 9/2 12:00，
        # 所以第一次触发是 9/3 09:00 北京 = 9/3 01:00 UTC。
        assert utc_times[0] == datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
        assert local_times[0].startswith("2026-09-03T09:00:00+08:00")
        # 三次执行时间严格递增且间隔 24 小时。
        assert utc_times[1] == datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
        assert utc_times[2] == datetime(2026, 9, 5, 1, 0, tzinfo=UTC)

    def test_upcoming_fire_times_supports_step_expressions(self) -> None:
        # 注入时钟落在 04:03，避开整点边界：*/10 的下一次触发是 04:10 而不是当前时刻。
        runner = CronSchedule("Asia/Shanghai", clock=lambda: datetime(2026, 9, 2, 4, 3, tzinfo=UTC))
        utc_times, _ = runner.upcoming_fire_times("*/10 * * * *")
        assert len(utc_times) == 3
        assert utc_times[0] == datetime(2026, 9, 2, 4, 10, tzinfo=UTC)
        assert utc_times[1] == datetime(2026, 9, 2, 4, 20, tzinfo=UTC)

    @pytest.mark.parametrize(("expression", "expected"), [
        ("30 0 * * *", datetime(2026, 9, 2, 16, 30, tzinfo=UTC)),
        ("0 9 * * 0", datetime(2026, 9, 7, 1, 0, tzinfo=UTC)),
        ("0 9 * * sun", datetime(2026, 9, 6, 1, 0, tzinfo=UTC)),
        ("15 10 15 10 *", datetime(2026, 10, 15, 2, 15, tzinfo=UTC)),
        ("0 9 8-15 9 mon", datetime(2026, 9, 14, 1, 0, tzinfo=UTC)),
    ])
    def test_historical_calendar_semantics_and_cross_day_preview_match_plan(self, expression, expected) -> None:
        # 保留原星期编号（0 为周一）及日期与星期同时满足的规则，不换用其他 cron 语义。
        runner = self._runner()
        utc_times, local_times = runner.upcoming_fire_times(expression)
        job = SimpleNamespace(enabled=True, cron_expr=expression)
        assert utc_times[0] == expected == runner.planned_run_at(job)
        assert datetime.fromisoformat(local_times[0]).astimezone(UTC) == expected

    def test_parse_cron_rejects_invalid_expressions(self) -> None:
        runner = self._runner()
        with pytest.raises(ValueError):
            runner.parse_cron("not a cron")
        with pytest.raises(ValueError):
            runner.parse_cron("99 * * * *")
