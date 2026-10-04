"""可用模型用例与「目录里恰好有一个默认」不变量的离线验证。

真实 ORM、真实事务跑在内存 SQLite 上（照 ``tests/test_llm_provider_service.py`` 的做法），不连
PostgreSQL。这里钉的是工单列出的那些可观察结果：默认不变量七条规则各自的行为、停用/启用渠道
之后选择列表的变化、以及「同一份默认维护只在一处」那条跨表的入口。

**并发那一半不在这里测**：部分唯一索引是 PostgreSQL 方言的东西，SQLite 上验不出真并发（见下面
夹具里那段注释）。这里只看「先清后设」的结果——设完之后只有一行 ``is_default`` 为真；索引本身
由 ``test_llm_models_migration.py`` 做结构断言、由真库上的 ``alembic check`` 兜底。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_lab.models.llm_model import LlmModelRecord
from agent_lab.models.llm_provider import LlmProviderRecord
from agent_lab.schemas.llm_models import LlmModelCreateRequest, LlmModelUpdateRequest
from agent_lab.schemas.llm_providers import (
    LlmProviderCreateRequest,
    LlmProviderUpdateRequest,
)
from agent_lab.services.llm_model_errors import (
    LlmModelDefaultCannotBeClearedError,
    LlmModelDefaultCannotBeDisabledError,
    LlmModelDefaultConflictError,
    LlmModelDefaultNotAvailableError,
    LlmModelNameConflictError,
    LlmModelNotFoundError,
    LlmModelProviderDisabledError,
)
from agent_lab.services.llm_model_service import LlmModelService
from agent_lab.services.llm_provider_errors import (
    LlmProviderInUseAsDefaultError,
    LlmProviderNotFoundError,
)
from agent_lab.services.llm_provider_service import LlmProviderService

BASE_URL = "http://127.0.0.1:11434"
WINDOW = 32768
# 加行时刻用的基准。夹具会显式写 ``created_at``（见 ``add_records_at``），所以这些值只需要
# 彼此可比，不要求贴近真实时间。
ADDED_AT = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


@asynccontextmanager
async def catalog_database():
    """内存 SQLite 上的真实 ``llm_providers`` 与 ``llm_models`` 两张表。

    ``uq_llm_models_single_default`` 这条**部分唯一索引在建表前被摘掉**：``postgresql_where``
    是 PG 方言的谓词，SQLite 不认识它，``create_all`` 会把它建成一个「整列唯一」的索引——于是
    插入第二行 ``is_default=false`` 直接失败，整个夹具都用不了。本文件测的是业务规则，不是
    并发；那条索引在真库上的验收靠 ``alembic check`` 与迁移的结构断言。同名的唯一约束留着，
    它才是本文件要反复触发的那一个。

    Yields:
        绑定这个内存库的 ``async_sessionmaker``。
    """

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (LlmProviderRecord, LlmModelRecord):
        model.__table__.to_metadata(metadata)
    metadata.tables["llm_models"].indexes = {
        index
        for index in metadata.tables["llm_models"].indexes
        if index.name != "uq_llm_models_single_default"
    }
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield sessions
    finally:
        await engine.dispose()


async def add_provider(sessions, *, name: str = "本地渠道", enabled: bool = True):
    """用真实 Service 新增一条渠道；用 ollama 是因为它不要求凭据，省掉一套加密夹具。"""

    async with sessions() as session:
        return await LlmProviderService(session).create_provider(
            LlmProviderCreateRequest(
                name=name, provider="ollama", base_url=BASE_URL, enabled=enabled
            )
        )


async def set_provider_enabled(sessions, provider_id, enabled: bool):
    """用真实 Service 改渠道的启用位（这是「模型跟着变得可用」那条入口）。"""

    async with sessions() as session:
        return await LlmProviderService(session).update_provider(
            provider_id, LlmProviderUpdateRequest(enabled=enabled)
        )


def body(provider_id, **overrides: Any) -> LlmModelCreateRequest:
    """造一个新增模型的请求；``overrides`` 覆盖单个字段。"""

    return LlmModelCreateRequest(
        **{
            "provider_id": provider_id,
            "upstream_model_name": "qwen2.5:7b",
            "context_window": WINDOW,
            **overrides,
        }
    )


async def create(sessions, provider_id, **overrides: Any):
    """用真实 Service 新增一条模型。"""

    async with sessions() as session:
        return await LlmModelService(session).create_model(body(provider_id, **overrides))


async def update(sessions, model_id, **overrides: Any):
    """用真实 Service 改一条模型。"""

    async with sessions() as session:
        return await LlmModelService(session).update_model(
            model_id, LlmModelUpdateRequest(**overrides)
        )


async def stored_models(sessions) -> list[LlmModelRecord]:
    """按添加顺序读出全部模型行。"""

    async with sessions() as session:
        return list(
            (await session.scalars(
                select(LlmModelRecord).order_by(LlmModelRecord.created_at, LlmModelRecord.id)
            )).all()
        )


async def stored_model(sessions, model_id) -> LlmModelRecord:
    """按 id 读一条模型行。"""

    async with sessions() as session:
        return await session.get(LlmModelRecord, model_id)


async def stored_provider(sessions, provider_id) -> LlmProviderRecord:
    """按 id 读一条渠道行。"""

    async with sessions() as session:
        return await session.get(LlmProviderRecord, provider_id)


async def defaults(sessions) -> list[LlmModelRecord]:
    """读当前所有「默认」行；不变量要求它最多一条。"""

    async with sessions() as session:
        return list(
            (await session.scalars(
                select(LlmModelRecord).where(LlmModelRecord.is_default.is_(True))
            )).all()
        )


async def available(sessions) -> list[Any]:
    """用户选择列表（Service 那一层的结果）。"""

    async with sessions() as session:
        return await LlmModelService(session).list_available_models()


async def listed(sessions) -> list[Any]:
    """后台管理列表（Service 那一层的结果）。"""

    async with sessions() as session:
        return await LlmModelService(session).list_models()


async def add_records_at(sessions, provider_id, rows: list[dict[str, Any]]) -> None:
    """直接建行，并**显式写** ``created_at``。

    为什么绕过 Service 建行：SQLite 的 ``CURRENT_TIMESTAMP`` 只精确到秒，同一秒建的多条模型
    ``created_at`` 一模一样，而「同一批里多条同时变为可用时取最早添加的那一条」这条规则必须
    靠可区分的添加时刻才验得出来。这些行是**夹具**，被验的动作（启用渠道、设默认）仍然走真实
    Service。
    """

    async with sessions() as session:
        for row in rows:
            session.add(
                LlmModelRecord(
                    provider_id=provider_id,
                    context_window=WINDOW,
                    is_default=False,
                    enabled=True,
                    **row,
                )
            )
        await session.commit()


def test_creating_an_enabled_model_makes_it_the_default_and_lists_it_everywhere() -> None:
    """新建一条启用的模型：它成为默认，并同时出现在后台列表与用户选择列表里。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, name="本地渠道")
            created = await create(sessions, provider.id)
            record = await stored_model(sessions, created.model.id)

            assert record.upstream_model_name == "qwen2.5:7b"
            assert record.display_name is None
            assert record.context_window == WINDOW
            assert record.enabled is True
            assert record.is_default is True

            (view,) = await listed(sessions)
            assert (view.model.id, view.provider_name, view.provider_enabled) == (
                provider.id and record.id,
                "本地渠道",
                True,
            )
            (option,) = await available(sessions)
            assert option.model.id == record.id
            assert option.provider_name == "本地渠道"

    run(scenario())


def test_a_display_name_can_be_set_cleared_and_blank_means_no_name() -> None:
    """展示名留空就是「没有展示名」：新建、改回空、以及不传这个字段各是各的意思。

    选择器要显示什么由这条语义决定：没有展示名时它显示上游模型名（回落那一步在路由层，
    见 ``test_llm_models_api``）。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            created = await create(sessions, provider.id, display_name="演示模型")
            assert (await stored_model(sessions, created.model.id)).display_name == "演示模型"

            blank = await create(
                sessions, provider.id, upstream_model_name="qwen2.5:14b", display_name="   "
            )
            assert (await stored_model(sessions, blank.model.id)).display_name is None

            await update(sessions, created.model.id, display_name="")
            assert (await stored_model(sessions, created.model.id)).display_name is None

            await update(sessions, created.model.id, display_name="改回来")
            # 请求里没带这个字段 = 不改；带了空串 = 改成没有展示名。
            await update(sessions, created.model.id, context_window=65536)
            record = await stored_model(sessions, created.model.id)
            assert (record.display_name, record.context_window) == ("改回来", 65536)

    run(scenario())


def test_the_same_upstream_model_name_cannot_repeat_inside_one_provider() -> None:
    """同一渠道内上游模型名不可重复；换一条渠道就可以重名。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            first = await add_provider(sessions, name="甲渠道")
            second = await add_provider(sessions, name="乙渠道")
            await create(sessions, first.id)

            with pytest.raises(LlmModelNameConflictError):
                await create(sessions, first.id)
            assert len(await stored_models(sessions)) == 1

            await create(sessions, second.id)
            assert len(await stored_models(sessions)) == 2

    run(scenario())


def test_renaming_a_model_onto_an_existing_name_is_rejected_too() -> None:
    """把一条模型改成同渠道里已有的上游模型名同样被拒。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            created = await create(sessions, provider.id)
            other = await create(
                sessions, provider.id, upstream_model_name="qwen2.5:14b"
            )

            with pytest.raises(LlmModelNameConflictError):
                await update(
                    sessions, other.model.id, upstream_model_name="qwen2.5:7b"
                )
            assert (
                await stored_model(sessions, other.model.id)
            ).upstream_model_name == "qwen2.5:14b"
            assert (await stored_model(sessions, created.model.id)).is_default is True

    run(scenario())


def test_the_first_model_to_become_available_is_the_default_and_only_one_is_default() -> None:
    """第一条「变为可用」的模型自动成为默认，此后新加的条目不会抢走默认。

    两个入口都在这里：新建时就是启用的（第一条），以及后来被置为启用的（第二条）。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            # 先加一条停用的：它还不算可用，目录里于是仍然没有默认
            dormant = await create(
                sessions, provider.id, upstream_model_name="qwen2.5:1.5b", enabled=False
            )
            assert (await defaults(sessions)) == []
            assert await available(sessions) == []

            first = await create(sessions, provider.id)
            assert (await stored_model(sessions, first.model.id)).is_default is True

            # 再加一条启用的：默认不换人
            await create(sessions, provider.id, upstream_model_name="qwen2.5:14b")
            assert [row.id for row in await defaults(sessions)] == [first.model.id]

            # 把先前那条停用的置为启用：默认仍然只有一个、还是最早那条
            await update(sessions, dormant.model.id, enabled=True)
            assert [row.id for row in await defaults(sessions)] == [first.model.id]
            assert len(await available(sessions)) == 3

    run(scenario())


def test_enabling_a_provider_that_was_created_disabled_backfills_the_default() -> None:
    """先建停用的渠道、在里面建启用模型、再启用渠道 → 那条模型自动成为默认。

    这是「第一条变为可用的模型自动成为默认」的第三个入口。漏掉它，就会漏出「有可用模型、
    却没有默认」这个被声明为不可达的状态；它也是「默认维护只在一处、另一条路调它」的证据——
    这条用例走的是**渠道**那条路。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, enabled=False)
            created = await create(sessions, provider.id)

            # 渠道停着：模型自己启用，但它对用户不可用，所以不算「可用模型」，也不该当默认
            assert (await stored_model(sessions, created.model.id)).enabled is True
            assert (await stored_model(sessions, created.model.id)).is_default is False
            assert await available(sessions) == []
            assert (await defaults(sessions)) == []

            await set_provider_enabled(sessions, provider.id, True)

            assert (await stored_model(sessions, created.model.id)).is_default is True
            assert [view.model.id for view in await available(sessions)] == [created.model.id]

    run(scenario())


def test_disabling_a_provider_hides_its_models_and_enabling_it_brings_them_back() -> None:
    """停用渠道 → 它下面的模型从选择列表消失；再启用 → 原先启用的那些自动回到可选。

    这条同时证伪「停用渠道时逐个改模型的启用位」那种写法：模型自己的 ``enabled`` 全程没被碰过，
    它只反映「管理员有没有单独停用这条模型」。默认落在另一条渠道的模型上，所以停这条渠道不会
    被不变量拦下。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            keeper = await add_provider(sessions, name="常开渠道")
            default_model = await create(sessions, keeper.id)
            toggled = await add_provider(sessions, name="待停渠道")
            other = await create(
                sessions, toggled.id, upstream_model_name="qwen2.5:14b"
            )
            assert (await stored_model(sessions, other.model.id)).is_default is False

            await set_provider_enabled(sessions, toggled.id, False)
            assert [view.model.id for view in await available(sessions)] == [default_model.model.id]
            # 停的是渠道，不是模型：模型自己的启用位一个都没动（这正是「两份状态必然漂移」要躲的事）
            assert (await stored_model(sessions, other.model.id)).enabled is True
            # 默认还在，而且它没有被这条改动影响
            assert [row.id for row in await defaults(sessions)] == [default_model.model.id]

            await set_provider_enabled(sessions, toggled.id, True)
            assert {view.model.id for view in await available(sessions)} == {
                default_model.model.id,
                other.model.id,
            }

    run(scenario())


def test_the_earliest_added_model_wins_when_several_become_available_at_once() -> None:
    """多条同时变为可用时，默认落在**最早添加**的那一条。

    三条的 id 顺序与添加时刻顺序刻意相反：只用 id 排、或按插入顺序取的那种实现会挑错人，
    而「先添加的先当默认」才是管理员能预期的规则。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, enabled=False)
            newest = UUID("00000000-0000-4000-8000-000000000001")
            earliest = UUID("00000000-0000-4000-8000-000000000002")
            middle = UUID("00000000-0000-4000-8000-000000000003")
            await add_records_at(
                sessions,
                provider.id,
                [
                    {
                        "id": newest,
                        "upstream_model_name": "later-added",
                        "created_at": ADDED_AT + timedelta(minutes=2),
                        "updated_at": ADDED_AT + timedelta(minutes=2),
                    },
                    {
                        "id": earliest,
                        "upstream_model_name": "first-added",
                        "created_at": ADDED_AT,
                        "updated_at": ADDED_AT,
                    },
                    {
                        "id": middle,
                        "upstream_model_name": "middle-added",
                        "created_at": ADDED_AT + timedelta(minutes=1),
                        "updated_at": ADDED_AT + timedelta(minutes=1),
                    },
                ],
            )

            await set_provider_enabled(sessions, provider.id, True)

            assert [row.id for row in await defaults(sessions)] == [earliest]
            assert len(await available(sessions)) == 3

    run(scenario())


def test_the_smaller_id_wins_when_two_models_are_added_at_the_same_moment() -> None:
    """同一时刻添加的多条按 id 定先後——规则要给出确定答案，不靠库返回行的顺序。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, enabled=False)
            larger = UUID("ffffffff-0000-4000-8000-000000000002")
            smaller = UUID("00000000-0000-4000-8000-000000000001")
            await add_records_at(
                sessions,
                provider.id,
                [
                    {
                        "id": larger,
                        "upstream_model_name": "same-moment-b",
                        "created_at": ADDED_AT,
                        "updated_at": ADDED_AT,
                    },
                    {
                        "id": smaller,
                        "upstream_model_name": "same-moment-a",
                        "created_at": ADDED_AT,
                        "updated_at": ADDED_AT,
                    },
                ],
            )

            await set_provider_enabled(sessions, provider.id, True)

            assert [row.id for row in await defaults(sessions)] == [smaller]

    run(scenario())


def test_a_default_flag_is_rejected_when_the_model_is_not_available() -> None:
    """新建时不允许给一个不可用的模型打默认标记：自己停用、或所属渠道停用，两条都拒。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            enabled_provider = await add_provider(sessions, name="甲渠道")
            with pytest.raises(LlmModelDefaultNotAvailableError):
                await create(sessions, enabled_provider.id, is_default=True, enabled=False)
            assert await stored_models(sessions) == []

            disabled_provider = await add_provider(sessions, name="乙渠道", enabled=False)
            with pytest.raises(LlmModelDefaultNotAvailableError):
                await create(sessions, disabled_provider.id, is_default=True)
            assert await stored_models(sessions) == []

            # 同一次请求里把默认和启用一起给上就可以：判的是**保存之后**的状态
            created = await create(sessions, enabled_provider.id, is_default=True, enabled=True)
            assert (await stored_model(sessions, created.model.id)).is_default is True

    run(scenario())


def test_setting_the_default_requires_the_model_to_be_available() -> None:
    """把某个模型设为默认时，它必须当前可用；改到同一次请求里一起启用就可以。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            dormant = await create(sessions, provider.id, enabled=False)

            with pytest.raises(LlmModelDefaultNotAvailableError):
                await update(sessions, dormant.model.id, is_default=True)
            assert (await defaults(sessions)) == []

            await update(sessions, dormant.model.id, enabled=True, is_default=True)
            assert [row.id for row in await defaults(sessions)] == [dormant.model.id]

    run(scenario())


def test_a_model_under_a_disabled_provider_cannot_be_made_the_default() -> None:
    """所属渠道停用时，把它下面的模型设为默认同样被拒。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, enabled=False)
            created = await create(sessions, provider.id)

            with pytest.raises(LlmModelDefaultNotAvailableError):
                await update(sessions, created.model.id, is_default=True)
            assert (await stored_model(sessions, created.model.id)).is_default is False

    run(scenario())


def test_the_default_flag_cannot_be_cleared_by_a_request() -> None:
    """请求把当前默认的标记置假 → 被拒；标记与库里的行都不动。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            created = await create(sessions, provider.id)
            assert (await stored_model(sessions, created.model.id)).is_default is True

            with pytest.raises(LlmModelDefaultCannotBeClearedError):
                await update(sessions, created.model.id, is_default=False)

            assert (await stored_model(sessions, created.model.id)).is_default is True
            assert [row.id for row in await defaults(sessions)] == [created.model.id]

    run(scenario())


def test_the_default_model_cannot_be_disabled_and_neither_can_its_provider() -> None:
    """停用当前默认模型 → 被拒；停用它所属的渠道 → 被拒。

    拒的是「把默认一起带走」这件事，不是「这条渠道永远不能停」：默认换到别处之后就能停。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions, name="常开渠道")
            created = await create(sessions, provider.id)

            with pytest.raises(LlmModelDefaultCannotBeDisabledError):
                await update(sessions, created.model.id, enabled=False)
            assert (await stored_model(sessions, created.model.id)).enabled is True

            with pytest.raises(LlmProviderInUseAsDefaultError):
                await set_provider_enabled(sessions, provider.id, False)
            assert (await stored_provider(sessions, provider.id)).enabled is True

            # 把默认换到另一条渠道的模型上，原渠道就能停了
            other_provider = await add_provider(sessions, name="备用渠道")
            other = await create(sessions, other_provider.id, upstream_model_name="qwen2.5:14b")
            await update(sessions, other.model.id, is_default=True)
            await set_provider_enabled(sessions, provider.id, False)
            assert (await stored_provider(sessions, provider.id)).enabled is False
            assert [row.id for row in await defaults(sessions)] == [other.model.id]

    run(scenario())


def test_a_model_cannot_be_moved_to_a_disabled_or_unknown_provider() -> None:
    """把模型改挂到一条停用的渠道 → 被拒；挂到不存在的渠道 → 当渠道不存在处理。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            source = await add_provider(sessions, name="甲渠道")
            disabled = await add_provider(sessions, name="乙渠道", enabled=False)
            created = await create(sessions, source.id)

            with pytest.raises(LlmModelProviderDisabledError):
                await update(sessions, created.model.id, provider_id=disabled.id)
            assert (await stored_model(sessions, created.model.id)).provider_id == source.id

            with pytest.raises(LlmProviderNotFoundError):
                await update(sessions, created.model.id, provider_id=uuid4())
            assert (await stored_model(sessions, created.model.id)).provider_id == source.id

            # 改挂到一条启用的渠道是允许的，默认标记跟着这条模型走
            target = await add_provider(sessions, name="丙渠道")
            await update(sessions, created.model.id, provider_id=target.id)
            record = await stored_model(sessions, created.model.id)
            assert (record.provider_id, record.is_default) == (target.id, True)

    run(scenario())


def test_setting_the_default_clears_the_previous_one_in_the_same_transaction() -> None:
    """设默认的结果是「旧的变假、新的为真」，全目录只有一行默认。

    真并发下这条由库上的部分唯一索引兜底（落败的一方拿到 ``LlmModelDefaultConflictError``，
    见 ``test_llm_models_api``）；这里钉的是「先清后设」这件事本身的结果。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            first_provider = await add_provider(sessions, name="甲渠道")
            first = await create(sessions, first_provider.id)
            second_provider = await add_provider(sessions, name="乙渠道")
            second = await create(sessions, second_provider.id, upstream_model_name="qwen2.5:14b")
            assert [row.id for row in await defaults(sessions)] == [first.model.id]

            await update(sessions, second.model.id, is_default=True)

            assert [row.id for row in await defaults(sessions)] == [second.model.id]
            assert (await stored_model(sessions, first.model.id)).is_default is False
            assert (await stored_model(sessions, first.model.id)).enabled is True

    run(scenario())


def test_reading_an_unknown_model_raises_not_found() -> None:
    """改一条不存在的模型抛领域错误（HTTP 层据此回 404）。"""

    async def scenario() -> None:
        async with catalog_database() as sessions:
            async with sessions() as session:
                with pytest.raises(LlmModelNotFoundError):
                    await LlmModelService(session).update_model(
                        uuid4(), LlmModelUpdateRequest(enabled=False)
                    )

    run(scenario())


def test_a_lost_race_while_backfilling_the_default_is_a_conflict_not_a_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补默认撞上并发设默认：给一个明确的冲突，而不是「存储不可用」。

    库上那条部分唯一索引把落败的一方拦在 ``commit`` 上；那个 ``IntegrityError`` 如果不翻，
    会顺着 ``SQLAlchemyError`` 那条路变成 503「模型目录存储当前不可用」——一个把管理员引向
    「去查数据库」的错误结论。

    只能报冲突、不能吞掉：补默认的 ``commit`` 失败会把**调用方那一笔一起回滚**，而其中一条
    调用路是「启用渠道」。吞掉就等于「点了启用却没启用」，下次打开页面又是停用的。

    真并发在 SQLite 上演不出来（见夹具那段注释），所以这里把那个 ``IntegrityError`` 直接造在
    提交那一步上，验的是**它的去向**：与显式设默认同一个错误。
    """

    async def scenario() -> None:
        async with catalog_database() as sessions:
            provider = await add_provider(sessions)
            # 绕过 Service 建一行可用的模型：于是目录里有可用模型、却还没有默认
            await add_records_at(sessions, provider.id, [{"upstream_model_name": "qwen2.5:7b"}])
            assert await defaults(sessions) == []

            async def failing_commit() -> None:
                raise IntegrityError("UPDATE llm_models", {}, Exception("duplicate key"))

            monkeypatch.setattr(
                "agent_lab.repositories.llm_model_repository.LlmModelRepository.commit",
                lambda _self: failing_commit(),
            )

            async with sessions() as session:
                with pytest.raises(LlmModelDefaultConflictError):
                    await LlmModelService(session).ensure_default_model()

    run(scenario())
