"""账号创建与 Pipeline CLI 参数、生命周期、退出码和脱敏输出的离线测试。"""

import argparse
import asyncio
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

import agent_lab.cli as cli_module
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.cli import CommandOutcome, build_parser, main
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.services.news_pipeline_execution_service import (
    IndexExecutionFailure,
    NewsSyncExecutionResult,
    PendingIndexExecutionResult,
)
from agent_lab.knowledge.document_contracts import SourceSyncFailure
from tests.test_agent_thread_messages import history_database, read_rows


def run(coroutine: Any) -> Any:
    """执行一个 CLI 测试协程。"""

    return asyncio.run(coroutine)


def test_parser_exposes_auth_and_pipeline_commands_with_conservative_defaults() -> None:
    parser = build_parser()

    create_user = parser.parse_args(
        ["create-user", "--email", "reader@example.com", "--superuser"]
    )
    sync = parser.parse_args(["sync-news"])
    index = parser.parse_args(["index-pending"])
    once = parser.parse_args(["run-once"])

    assert create_user.email == "reader@example.com"
    assert create_user.superuser is True
    assert sync.limit_per_source == 2
    assert index.batch_size == 20
    assert index.stale_after_minutes == 60
    assert once.limit_per_source == 2
    assert once.batch_size == 20
    assert once.stale_after_minutes == 60


def test_create_user_prompts_twice_and_never_returns_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    entered = iter(["a-long-local-password", "a-long-local-password"])
    created: list[Any] = []

    class FakeSessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: Any) -> None:
            pass

    class FakeManager:
        def __init__(self, _database: Any) -> None:
            pass

        async def create(self, create: Any) -> Any:
            created.append(create)
            return SimpleNamespace(
                id=uuid4(),
                email=str(create.email),
                is_superuser=create.is_superuser,
            )

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return next(entered)

    monkeypatch.setattr(cli_module, "getpass", fake_getpass)
    monkeypatch.setattr(cli_module, "async_session_factory", lambda: FakeSessionContext())
    monkeypatch.setattr(cli_module, "SQLAlchemyUserDatabase", lambda *_args: object())
    monkeypatch.setattr(cli_module, "UserManager", FakeManager)

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(
                ["create-user", "--email", "reader@example.com", "--superuser"]
            )
        )
    )

    assert prompts == ["Password: ", "Confirm password: "]
    assert len(created) == 1
    assert created[0].password == "a-long-local-password"
    assert created[0].is_verified is True
    assert outcome.exit_code == 0
    assert outcome.payload["email"] == "reader@example.com"
    assert outcome.payload["is_superuser"] is True
    assert "password" not in outcome.payload


def test_create_user_rejects_mismatched_confirmation_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = iter(["first-long-password", "second-long-password"])
    monkeypatch.setattr(cli_module, "getpass", lambda _prompt: next(entered))
    monkeypatch.setattr(
        cli_module,
        "async_session_factory",
        lambda: pytest.fail("database must not be opened"),
    )

    args = build_parser().parse_args(
        ["create-user", "--email", "reader@example.com"]
    )
    with pytest.raises(cli_module.PasswordConfirmationError):
        run(cli_module.dispatch_command(args))


@pytest.mark.parametrize(
    "argv",
    [
        ["sync-news", "--limit-per-source", "0"],
        ["sync-news", "--limit-per-source", "101"],
        ["index-pending", "--batch-size", "0"],
        ["index-pending", "--batch-size", "1001"],
        ["index-pending", "--stale-after-minutes", "0"],
    ],
)
def test_parser_rejects_unbounded_or_zero_work(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code == 2


def test_index_command_uses_shared_processing_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    expected = PendingIndexExecutionResult(
        candidate_count=0,
        requeued_stale_count=0,
        indexed_count=0,
        skipped_count=0,
        failures=(),
    )

    class FakeBatch:
        async def run(self, **kwargs: Any) -> Any:
            assert kwargs["batch_size"] == 3
            events.append("index_pending")
            return expected

    monkeypatch.setattr(cli_module, "build_pipeline_write_runtime", lambda: PipelineWriteRuntime(
        executor=object(), processing_factory=FakeBatch,
    ))
    args = build_parser().parse_args(["index-pending", "--batch-size", "3"])
    result = run(cli_module.dispatch_command(args))
    assert result.exit_code == 0
    assert events == ["index_pending"]
    assert result.payload["review_documents"] == 0


def test_index_command_closes_its_runtime_when_processing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeRuntime:
        async def index_only(self, **_kwargs) -> None:
            events.append("process")
            raise RuntimeError("不得打印远端响应内容")

        async def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(cli_module, "build_pipeline_write_runtime", FakeRuntime)
    args = build_parser().parse_args(["index-pending"])

    with pytest.raises(RuntimeError, match="远端响应"):
        run(cli_module.dispatch_command(args))

    assert events == ["process", "close"]


def test_dispatch_sync_news_never_builds_qdrant_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeExecutor:
        def __init__(self, _session_factory: Any) -> None:
            events.append("executor")

        async def sync_news(self, _service: Any, **kwargs: Any) -> Any:
            events.append(f"sync:{kwargs['limit_per_source']}")
            return NewsSyncExecutionResult(synchronized_count=5)

    monkeypatch.setattr(cli_module, "build_pipeline_write_runtime", lambda: PipelineWriteRuntime(
        executor=FakeExecutor(None), import_service=object(),
        processing_factory=lambda: pytest.fail("同步不能创建索引依赖"),
    ))

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["sync-news", "--limit-per-source", "3"])
        )
    )

    assert outcome.exit_code == 0
    assert outcome.payload["synchronized_documents"] == 5
    assert events == ["executor", "sync:3"]


def test_dispatch_run_once_syncs_before_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeExecutor:
        def __init__(self, _session_factory: Any) -> None:
            pass

        async def sync_news(self, _service: Any, **_kwargs: Any) -> Any:
            events.append("sync")
            return NewsSyncExecutionResult(synchronized_count=2)

    async def fake_index(_executor: Any, _args: Any) -> Any:
        events.append("index")
        return PendingIndexExecutionResult(
            candidate_count=1,
            requeued_stale_count=0,
            indexed_count=1,
            skipped_count=0,
            failures=(),
        )


    class FakeRuntime:
        async def run_once(self, **_kwargs):
            executor = FakeExecutor(None)
            return SimpleNamespace(sync=await executor.sync_news(None), index=await fake_index(executor, None))

        async def close(self):
            pass

    monkeypatch.setattr(cli_module, "build_pipeline_write_runtime", FakeRuntime)

    outcome = run(
        cli_module.dispatch_command(build_parser().parse_args(["run-once"]))
    )

    assert events == ["sync", "index"]
    assert outcome.payload["synchronized_documents"] == 2
    assert outcome.payload["indexed_documents"] == 1


def test_run_once_continues_indexing_but_fails_when_one_source_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeExecutor:
        def __init__(self, _session_factory: Any) -> None:
            pass

        async def sync_news(self, _service: Any, **_kwargs: Any) -> Any:
            events.append("sync")
            return NewsSyncExecutionResult(
                synchronized_count=1,
                source_count=2,
                successful_source_count=1,
                checkpoint_advanced_count=1,
                failures=(
                    SourceSyncFailure(
                        source_external_id="feed/failed",
                        error_type="FreshRSSConnectionError",
                    ),
                ),
            )

    async def fake_index(_executor: Any, _args: Any) -> Any:
        events.append("index")
        return PendingIndexExecutionResult(
            candidate_count=1,
            requeued_stale_count=0,
            indexed_count=1,
            skipped_count=0,
            failures=(),
        )


    class FakeRuntime:
        async def run_once(self, **_kwargs):
            executor = FakeExecutor(None)
            return SimpleNamespace(sync=await executor.sync_news(None), index=await fake_index(executor, None))

        async def close(self):
            pass

    monkeypatch.setattr(cli_module, "build_pipeline_write_runtime", FakeRuntime)

    outcome = run(cli_module.dispatch_command(build_parser().parse_args(["run-once"])))

    assert events == ["sync", "index"]
    assert outcome.exit_code == 1
    assert outcome.payload["ok"] is False
    assert outcome.payload["failed_sync_sources"] == 1
    assert outcome.payload["sync_failures"] == [
        {
            "source_external_id": "feed/failed",
            "error_type": "FreshRSSConnectionError",
        }
    ]


def test_index_outcome_is_nonzero_and_contains_no_exception_text() -> None:
    document_id = uuid4()
    result = PendingIndexExecutionResult(
        candidate_count=1,
        requeued_stale_count=0,
        indexed_count=0,
        skipped_count=0,
        failures=(
            IndexExecutionFailure(
                document_id=document_id,
                error_type="RuntimeError",
            ),
        ),
    )

    outcome = cli_module._index_outcome("index-pending", result)  # noqa: SLF001

    assert outcome.exit_code == 1
    assert outcome.payload["ok"] is False
    assert outcome.payload["failed_documents"] == 1
    assert outcome.payload["failures"] == [
        {"document_id": str(document_id), "error_type": "RuntimeError"}
    ]


def test_main_prints_machine_readable_success_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(_args: argparse.Namespace) -> CommandOutcome:
        return CommandOutcome(
            payload={"command": "sync-news", "ok": True},
            exit_code=0,
        )

    monkeypatch.setattr(cli_module, "_run_with_cleanup", fake_run)

    assert main(["sync-news"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"command": "sync-news", "ok": True}


def test_main_reports_only_exception_type_on_command_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(_args: argparse.Namespace) -> CommandOutcome:
        raise RuntimeError("API 密钥与完整远端响应")

    monkeypatch.setattr(cli_module, "_run_with_cleanup", fake_run)

    assert main(["sync-news"]) == 1
    captured = capsys.readouterr()
    assert "api-key" not in captured.err
    assert "full remote response" not in captured.err
    assert json.loads(captured.err) == {
        "command": "sync-news",
        "error_type": "RuntimeError",
        "ok": False,
    }


# --- prune-orphan-threads ------------------------------------------------------
#
# 这个命令**不可恢复地删除**会话历史，所以下面的用例重点不在「能不能删」，而在「会不会删错」：
# 有归属记录的会话一条都不能碰，而且默认不许真删。


class _FakeThreadService:
    """只回答会话归属表与会话历史表里各有哪些 thread_id 的替身。"""

    def __init__(self, known: set[Any], recorded: set[Any]) -> None:
        self._known = known
        self._recorded = recorded
        self.calls = 0
        # 真删时 ``delete_thread_messages`` 收到的 id 列表；没被调过就是 ``None``。
        self.deleted_messages: list[str] | None = None

    async def list_known_thread_ids(self) -> set[Any]:
        """记一次调用并返回预置集合。"""

        self.calls += 1
        return self._known

    async def list_message_thread_ids(self) -> set[Any]:
        """返回预置的「会话历史表里出现过的 id」。"""

        return self._recorded

    async def delete_thread_messages(self, thread_ids: list[str]) -> int:
        """记下收到的 id，不真删。"""

        self.deleted_messages = list(thread_ids)
        return len(self.deleted_messages)


def _patch_prune(
    monkeypatch: pytest.MonkeyPatch,
    *,
    known: set[Any],
    stored: set[str],
    recorded: set[Any] | None = None,
) -> dict[str, Any]:
    """把 prune 命令的四个外部依赖全换成替身。

    Args:
        monkeypatch: pytest 的替换工具。
        known: 业务表里的归属记录 id（可以是 UUID 对象，与真实实现一致）。
        stored: checkpointer 里存有历史的 thread_id（字符串，与真实实现一致）。
        recorded: 会话历史表里出现过的 thread_id；省略等同「那边一行都没有」。

    Returns:
        记录本次调用情况的字典，含 ``deleted``（真删时 checkpointer 收到的 id 列表）。

    Notes:
        两侧刻意用不同类型：业务表存 UUID 对象、checkpointer 存字符串，这正是真实情况。
        替身保持这个差异，才能测出「有没有统一成同一种类型再比」。
    """

    seen: dict[str, Any] = {"deleted": None, "delete_calls": 0}
    service = _FakeThreadService(known, recorded or set())

    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://unused/unused"),
    )
    monkeypatch.setattr(cli_module, "async_session_factory", object())
    monkeypatch.setattr(cli_module, "AgentThreadService", lambda _factory: service)

    async def fake_list(_database_url: str) -> set[str]:
        return stored

    async def fake_delete(_database_url: str, thread_ids: Any) -> int:
        seen["delete_calls"] += 1
        seen["deleted"] = list(thread_ids)
        return len(list(thread_ids))

    monkeypatch.setattr(cli_module, "list_checkpointer_thread_ids", fake_list)
    monkeypatch.setattr(cli_module, "delete_checkpointer_threads", fake_delete)
    seen["service"] = service
    return seen


def test_prune_orphan_threads_defaults_to_a_dry_run() -> None:
    """不加 ``--yes`` 时 ``yes`` 为假。

    这条是安全默认值的断言。默认真删的话，一次手误就把历史清了，而且没有回收站。
    """

    args = build_parser().parse_args(["prune-orphan-threads"])

    assert args.yes is False
    assert build_parser().parse_args(["prune-orphan-threads", "--yes"]).yes is True


def test_prune_dry_run_reports_the_count_without_deleting_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预演只报数：``deleted_threads`` 为 0，且删除函数一次都没被调用。

    只断言返回值里的 0 不够——一个「先删了再把计数写成 0」的实现同样通过。必须断言删除函数
    没被调过。
    """

    orphan = uuid4()
    seen = _patch_prune(monkeypatch, known=set(), stored={str(orphan)})

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads"])
        )
    )

    assert outcome.exit_code == 0
    assert outcome.payload == {
        "command": "prune-orphan-threads",
        "ok": True,
        "dry_run": True,
        "orphan_threads": 1,
        "deleted_threads": 0,
    }
    assert seen["delete_calls"] == 0


def test_prune_with_yes_deletes_exactly_the_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--yes`` 时只把孤儿传给删除函数，有归属记录的那个不在其中。

    这是本命令最危险的一步，所以断言的是**传给删除函数的确切集合**，不是数量。数量相同但删错
    对象的实现（比如把差集方向写反）在只数个数时照样通过。
    """

    owned = uuid4()
    orphan_one, orphan_two = uuid4(), uuid4()
    seen = _patch_prune(
        monkeypatch,
        known={owned},
        stored={str(owned), str(orphan_one), str(orphan_two)},
    )

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads", "--yes"])
        )
    )

    assert outcome.payload["dry_run"] is False
    assert outcome.payload["orphan_threads"] == 2
    assert outcome.payload["deleted_threads"] == 2
    assert set(seen["deleted"]) == {str(orphan_one), str(orphan_two)}
    assert str(owned) not in seen["deleted"]


def test_prune_does_not_treat_owned_threads_as_orphans_despite_type_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务表存 UUID 对象、checkpointer 存字符串，两边同一个会话不能被当成孤儿。

    **这条防的是一个会删掉全部历史的 bug。** 直接 ``set[str] - set[UUID]`` 永远等于左边全集，
    因为 ``UUID(...) != "..."``，于是每个会话都成了孤儿。加上 ``--yes`` 就是把所有人的历史清空，
    而命令还会报告「成功」。实现里靠 ``{str(x) for x in owned}`` 统一类型，这条钉住它。
    """

    owned = [uuid4(), uuid4(), uuid4()]
    seen = _patch_prune(
        monkeypatch,
        known=set(owned),
        stored={str(thread_id) for thread_id in owned},
    )

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads", "--yes"])
        )
    )

    assert outcome.payload["orphan_threads"] == 0
    assert seen["deleted"] == []


def test_prune_cleans_up_a_non_uuid_thread_id_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """checkpointer 里混进非 UUID 的 thread_id 时把它当孤儿清掉，而不是让整个命令炸掉。

    这种值只可能来自手工写库或别的实验，业务表里不可能有对应归属，本来就该清。若实现先
    ``UUID(...)`` 解析再比，一个脏值会让命令抛异常，连带真正的孤儿也清不掉。
    """

    seen = _patch_prune(monkeypatch, known=set(), stored={"手工塞进来的-id"})

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads", "--yes"])
        )
    )

    assert outcome.payload["orphan_threads"] == 1
    assert seen["deleted"] == ["手工塞进来的-id"]


def test_prune_reads_the_ownership_table_before_touching_the_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务表读不出来时直接失败，不进入删除逻辑。

    顺序在这里是安全属性：``agent_threads`` 查不到（比如迁移还没跑）时若把它当成空集，
    全部历史都会被判成孤儿。所以异常必须往上抛。
    """

    class _BrokenService:
        async def list_known_thread_ids(self) -> set[Any]:
            raise RuntimeError("业务库不可用")

    seen = _patch_prune(monkeypatch, known=set(), stored={str(uuid4())})
    monkeypatch.setattr(cli_module, "AgentThreadService", lambda _f: _BrokenService())

    with pytest.raises(RuntimeError):
        run(
            cli_module.dispatch_command(
                build_parser().parse_args(["prune-orphan-threads", "--yes"])
            )
        )

    assert seen["delete_calls"] == 0


def test_prune_dry_run_reports_a_leftover_that_only_the_history_table_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预演把「只有会话历史表里有行」的候选也报出来，而且一行都不删。

    候选集从两侧取并集（checkpointer 与 ``agent_thread_messages``），所以预演报的数也必须是并集
    算出来的那个——只看 checkpointer 的话这种残余一次都不会出现在报告里。
    """

    leftover = uuid4()
    seen = _patch_prune(monkeypatch, known=set(), stored=set(), recorded={str(leftover)})

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads"])
        )
    )

    assert outcome.payload == {
        "command": "prune-orphan-threads",
        "ok": True,
        "dry_run": True,
        "orphan_threads": 1,
        "deleted_threads": 0,
    }
    assert seen["delete_calls"] == 0
    assert seen["service"].deleted_messages is None


def test_prune_with_yes_removes_a_leftover_that_only_the_history_table_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--yes`` 时那种残余也要真删：checkpointer 那一步照走，会话历史表那一步按 id 清。

    这种行就是删一个正在运行的会话留下的（那次运行随后把收尾内容写了回来），它不在 checkpointer
    里，所以「按 thread_id 删会话历史表」这一步是它唯一的出口。
    """

    leftover = uuid4()
    seen = _patch_prune(monkeypatch, known=set(), stored=set(), recorded={str(leftover)})

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads", "--yes"])
        )
    )

    assert outcome.payload["dry_run"] is False
    assert outcome.payload["orphan_threads"] == 1
    assert set(seen["deleted"]) == {str(leftover)}
    assert seen["service"].deleted_messages == [str(leftover)]


def test_prune_never_treats_a_thread_with_an_ownership_row_as_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话历史表里有行不等于孤儿：有归属记录的会话一个都不能碰。

    候选集是「两侧取并集、再减去归属记录」。漏掉那个减法就是灾难——每个有历史的会话都会被当成
    孤儿，归属行和它的对话一起删掉，命令还会报告成功。
    """

    owned = uuid4()
    seen = _patch_prune(
        monkeypatch, known={owned}, stored={str(owned)}, recorded={str(owned)}
    )

    outcome = run(
        cli_module.dispatch_command(
            build_parser().parse_args(["prune-orphan-threads", "--yes"])
        )
    )

    assert outcome.payload["orphan_threads"] == 0
    assert seen["deleted"] == []
    assert seen["service"].deleted_messages == []


# --- 两个清理命令真删时动到的两张表（真实 Service 跑在临时 SQLite 上）-----------
#
# 上面的替身证明「候选集怎么算、把哪些 id 交给谁」；「表里的行真的没了」得由真实 Service 在真实
# 库上证明，所以下面两条把命令整个跑一遍。建表与读行借 tests/test_agent_thread_messages.py 的
# 夹具（那边是这张表语义的主场，不在这里复制第二份）。


async def _seed_thread(sessions: Any, *, thread_id: Any, last_active_at: Any) -> None:
    """直接写一行会话归属记录。

    本节的断言是「删会话之后那些行没了」，预置只要行存在就够；而「只有历史行、没有归属行」那种
    残余只有分开写才造得出来，所以两张表的预置分成两个函数。
    """

    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            AgentThreadRecord(
                thread_id=thread_id,
                user_id=uuid4(),
                title="预置会话",
                scope={"mode": "all"},
                llm_model_id=None,
                system_prompt=None,
                created_at=now,
                last_active_at=last_active_at,
                active_run_id=None,
                stop_requested_at=None,
                drained_at=None,
            )
        )
        await session.commit()


async def _seed_history_row(sessions: Any, *, thread_id: Any) -> None:
    """直接写一行会话历史行：它是删除时要连带删掉的那一侧。"""

    async with sessions() as session:
        session.add(
            AgentThreadMessageRecord(
                id=uuid4(),
                thread_id=thread_id,
                run_id=uuid4(),
                seq=0,
                role="question",
                text="预置的提问",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


def _patch_databases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sessions: Any,
    stored: set[str],
) -> dict[str, Any]:
    """把两个清理命令的库与 checkpointer 依赖换掉，业务库换成给定的 session 工厂。

    Args:
        monkeypatch: pytest 的替换工具。
        sessions: 真实 SQLite 上的 ``async_sessionmaker``。
        stored: checkpointer 那边存有历史的 thread_id。

    Returns:
        字典，``deleted`` 键里是真删时交给 checkpointer 清理的 id 列表。

    Notes:
        ``AgentThreadService`` **不替换**：本节要的就是真实 Service 真的去删那两张表。
    """

    seen: dict[str, Any] = {"deleted": []}

    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://unused/unused"),
    )
    monkeypatch.setattr(cli_module, "async_session_factory", sessions)

    async def fake_list(_database_url: str) -> set[str]:
        return stored

    async def fake_delete(_database_url: str, thread_ids: Any) -> int:
        seen["deleted"] = list(thread_ids)
        return len(list(thread_ids))

    monkeypatch.setattr(cli_module, "list_checkpointer_thread_ids", fake_list)
    monkeypatch.setattr(cli_module, "delete_checkpointer_threads", fake_delete)
    return seen


def test_prune_orphan_threads_deletes_a_leftover_that_only_the_history_table_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只存在于会话历史表的残余：预演报得出、``--yes`` 真删得掉。

    造这个场面：表里有该 thread 的历史行、没有任何归属行、checkpointer 里也没有它。删一个正在
    运行的会话就会留下这种残余（等它停下的上限到了删除继续，而那次运行随后把收尾内容写了回来），
    所以它必须能被这条命令收拾掉。
    """

    async def verify() -> tuple[dict[str, Any], list[Any], list[str], list[Any], str]:
        async with history_database(tmp_path) as sessions:
            leftover = uuid4()
            # 只有历史行：归属行直接不写，checkpointer 那边也当成空的。
            await _seed_history_row(sessions, thread_id=leftover)
            seen = _patch_databases(monkeypatch, sessions=sessions, stored=set())

            dry = await cli_module.dispatch_command(
                build_parser().parse_args(["prune-orphan-threads"])
            )
            after_dry = await read_rows(sessions, leftover)

            await cli_module.dispatch_command(
                build_parser().parse_args(["prune-orphan-threads", "--yes"])
            )
            return (
                dry.payload,
                after_dry,
                list(seen["deleted"]),
                await read_rows(sessions, leftover),
                str(leftover),
            )

    dry_payload, after_dry, checkpointer_ids, rows_after, leftover_id = run(verify())

    assert dry_payload["dry_run"] is True
    assert dry_payload["orphan_threads"] == 1 and dry_payload["deleted_threads"] == 0
    assert len(after_dry) == 1, "预演不该删任何行"
    assert checkpointer_ids == [leftover_id]
    assert rows_after == []


def test_prune_old_threads_deletes_the_history_rows_of_the_sessions_it_removes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧会话清理真删时：那些会话的历史行跟着没，没被选中的会话一行不动。

    连带删除在 ``AgentThreadService.delete_threads`` 里（同一个事务里先删历史行、再删归属行），
    这条用例从命令那一端把它走通：命令 → ``delete_threads`` → 两张表。
    """

    async def verify() -> tuple[dict[str, Any], list[str], list[Any], list[Any], str, str, set[Any]]:
        async with history_database(tmp_path) as sessions:
            stale, fresh = uuid4(), uuid4()
            await _seed_thread(
                sessions,
                thread_id=stale,
                last_active_at=datetime.now(UTC) - timedelta(days=30),
            )
            await _seed_history_row(sessions, thread_id=stale)
            await _seed_thread(sessions, thread_id=fresh, last_active_at=datetime.now(UTC))
            await _seed_history_row(sessions, thread_id=fresh)
            seen = _patch_databases(monkeypatch, sessions=sessions, stored=set())

            outcome = await cli_module.dispatch_command(
                build_parser().parse_args(
                    ["prune-old-threads", "--before-days", "7", "--yes"]
                )
            )
            return (
                outcome.payload,
                list(seen["deleted"]),
                await read_rows(sessions, stale),
                await read_rows(sessions, fresh),
                str(stale),
                str(fresh),
                await AgentThreadService(sessions).list_known_thread_ids(),
            )

    payload, checkpointer_ids, stale_rows, fresh_rows, stale_id, fresh_id, owned_left = run(verify())

    assert payload["dry_run"] is False
    assert payload["old_threads"] == 1 and payload["deleted_threads"] == 1
    assert checkpointer_ids == [stale_id]
    # 被选中的会话两张表的行都没了；没被选中的会话一行不动。
    assert stale_rows == []
    assert owned_left == {UUID(fresh_id)}
    assert [(row.role, row.text) for row in fresh_rows] == [("question", "预置的提问")]
