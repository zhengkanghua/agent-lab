"""「开始运行之前解析当轮模型」那道门的离线验证。

真实 ORM 与真实事务跑在内存 SQLite 上（复用 ``test_llm_model_service`` 的目录夹具：两张表的
真实建表语句 + 真实 Service 写数据），不连 PostgreSQL。这里钉的是这道门自己的可观察结果：

- 没选模型时用的是**默认且可用**的那一条，快照连展示名与上下文窗口一起给出；
- 选择指向目录里不存在的 id → ``LlmModelNotFoundError``（对外 404）；
- 选择指向一条自身停用、或所属渠道停用的条目 → ``LlmModelUnavailableError``（对外 409）；
- 目录为空或全部停用 → ``NoAvailableLlmModelsError``（对外 409，文案指向管理员）；
- 解析**每次都读表**：刚停用的模型立刻就不能被选中，不等任何缓存过期；
- ``describe_choice``（读回「原来是 xxx」那条路）只读，且容忍停用与不存在。

「只校验当轮生效的那一份」与「模型一次都没被调用」是 HTTP 层的断言，见
``tests/test_agent_thread_model_choice.py``：它们要同时看请求体、会话行与假模型的调用次数。
"""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from agent_lab.services.llm_model_errors import (
    LlmModelNotFoundError,
    LlmModelUnavailableError,
    NoAvailableLlmModelsError,
)
from agent_lab.services.llm_model_selection_service import LlmModelSelectionService
from tests.test_llm_model_service import (
    catalog_database,
    add_provider,
    create,
    update,
)

WINDOW = 32768


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


def service_for(sessions: Any) -> LlmModelSelectionService:
    """用同一份内存库的 session 工厂构造真实 Service（与生产同形：拿工厂、不拿 session）。"""

    return LlmModelSelectionService(sessions)


def test_a_session_without_a_selection_resolves_the_default_model() -> None:
    """没选过模型的会话拿到默认那一条，且快照里带着当时的展示名与窗口。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            model = await create(sessions, provider.id, display_name="演示模型")

            snapshot = await service_for(sessions).resolve_for_run(None)

            assert snapshot.id == model.model.id
            assert snapshot.display_name == "演示模型"
            assert snapshot.context_window == WINDOW

    run(scenario())


def test_a_blank_display_name_falls_back_to_the_upstream_model_name() -> None:
    """条目没填展示名时快照给的是上游模型名——回落只在服务端落一次。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            model = await create(sessions, provider.id, upstream_model_name="qwen2.5:7b")

            snapshot = await service_for(sessions).resolve_for_run(model.model.id)

            assert snapshot.display_name == "qwen2.5:7b"

    run(scenario())


def test_an_unknown_id_is_reported_as_not_found() -> None:
    """指到一个目录里没有的 id：404，不是「用默认模型对付过去」。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            await create(sessions, provider.id)

            with pytest.raises(LlmModelNotFoundError):
                await service_for(sessions).resolve_for_run(uuid4())

    run(scenario())


def test_a_disabled_entry_cannot_be_resolved() -> None:
    """这一条自己停用了：409，而且**不**回落到默认模型。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            await create(sessions, provider.id)
            stopped = await create(
                sessions, provider.id, upstream_model_name="qwen2.5:32b", enabled=False
            )

            with pytest.raises(LlmModelUnavailableError):
                await service_for(sessions).resolve_for_run(stopped.model.id)

    run(scenario())


def test_an_entry_under_a_disabled_provider_cannot_be_resolved() -> None:
    """所属渠道停了，挂在它下面的模型一起不可用（不去逐个改模型的启用位）。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, enabled=False)
            model = await create(sessions, provider.id)

            with pytest.raises(LlmModelUnavailableError):
                await service_for(sessions).resolve_for_run(model.model.id)

    run(scenario())


def test_an_empty_catalog_is_its_own_error() -> None:
    """一条模型都没配过：409，且是「没人可换、找管理员」那一条，不是「你选的那个不能用」。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            with pytest.raises(NoAvailableLlmModelsError):
                await service_for(sessions).resolve_for_run(None)

    run(scenario())


def test_a_catalog_where_everything_is_disabled_is_the_same_error() -> None:
    """全部停用与一条都没配过对用户是同一件事：他没得选。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            await create(sessions, provider.id, enabled=False)

            with pytest.raises(NoAvailableLlmModelsError):
                await service_for(sessions).resolve_for_run(None)

    run(scenario())


def test_a_just_disabled_model_stops_being_resolvable_at_once() -> None:
    """解析直接读表：停用之后下一个请求就选不到了，不等任何缓存过期。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            first = await create(sessions, provider.id)
            second = await create(sessions, provider.id, upstream_model_name="qwen2.5:32b")
            # 先把默认换到第二条，才停得掉第一条——默认模型不允许被停用。
            await update(sessions, second.model.id, is_default=True)

            service = service_for(sessions)
            assert (await service.resolve_for_run(first.model.id)).id == first.model.id

            await update(sessions, first.model.id, enabled=False)

            with pytest.raises(LlmModelUnavailableError):
                await service.resolve_for_run(first.model.id)

    run(scenario())


def test_describing_a_choice_reads_a_disabled_entry_and_tolerates_a_missing_one() -> None:
    """读回「原来是 xxx」时：停用的条目照样读得到名字，查不到的条目给 ``None``。

    这是回放接口与选择器显示失效态的那条路，所以它**不判可用性、也不抛错**——它读的正是
    那个已经不能被选中的条目。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            stopped = await create(
                sessions, provider.id, enabled=False, display_name="停用模型"
            )
            service = service_for(sessions)

            described = await service.describe_choice(stopped.model.id)

            assert described is not None
            assert (described.id, described.display_name) == (stopped.model.id, "停用模型")
            assert await service.describe_choice(uuid4()) is None
            assert await service.describe_choice(None) is None

    run(scenario())
