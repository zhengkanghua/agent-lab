"""``GET /usage/records`` 的离线契约测试（内存 SQLite + ASGI 传输）。

守的是明细接口的可观察行为：只返回当前账号的记录、分页不重不漏、同一毫秒的记录跨页稳定、
筛选参数真的生效、空结果是正常响应、用量库不可用时是稳定的 503 而不是空列表。

不连 PostgreSQL、不跑 lifespan 的真实用量库装配、不访问网络。真正的索引行为与真库时区解释由
``tests/test_usage_postgres_integration.py`` 负责。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.usage.contracts import UsageSource, UsageStatus

from tests.app_helpers import FakeSearchRuntime, OfflineUsageRuntime, create_offline_app, send
from tests.auth_helpers import READER_ID, SUPERUSER_ID, allow_reader
from tests.usage_helpers import add_records, make_record, usage_database


def run(coroutine: Any) -> Any:
    """执行异步测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def build_app(usage_runtime: Any) -> FastAPI:
    """装一个用量库资源可控、认证为普通账号的离线应用。"""

    app = create_offline_app(
        runtime_factory=FakeSearchRuntime,
        usage_runtime_factory=lambda: usage_runtime,
    )
    return allow_reader(app)


async def get_records(app: FastAPI, **params: Any) -> httpx.Response:
    """发一次明细查询。"""

    return await send(app, "GET", "/usage/records", params=params)


# 1、只返回当前账号的记录。


def test_only_the_current_accounts_records_are_returned() -> None:
    """另一个账号写入的记录在同一次请求里查不到。"""

    async def scenario() -> httpx.Response:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="mine"),
                make_record(user_id=SUPERUSER_ID, model_name="theirs"),
            ])
            return await get_records(build_app(OfflineUsageRuntime(sessions)))

    response = run(scenario())

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["model_name"] for item in items] == ["mine"]


def test_the_item_carries_the_contract_fields() -> None:
    """明细行带上排查一笔消耗需要的字段，缓存缺失回的是 null 而不是 0。"""

    call_id = uuid4()
    run_id = uuid4()
    thread_id = uuid4()

    async def scenario() -> dict[str, Any]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(
                    call_id=call_id,
                    user_id=READER_ID,
                    thread_id=thread_id,
                    run_id=run_id,
                    cached_tokens=None,
                    status=UsageStatus.FAILED,
                    source=UsageSource.MISSING,
                )
            ])
            response = await get_records(build_app(OfflineUsageRuntime(sessions)))
            return response.json()["items"][0]

    item = run(scenario())

    assert item["call_id"] == str(call_id)
    assert item["thread_id"] == str(thread_id)
    assert item["run_id"] == str(run_id)
    assert item["cached_tokens"] is None
    assert item["status"] == "failed"
    assert item["source"] == "missing"


# 2、分页：不重不漏，同一毫秒的记录也稳定。


def test_paging_neither_duplicates_nor_skips_records() -> None:
    """逐页取完，取到的调用标识集合恰好等于写入的集合。

    写入的记录全部落在同一毫秒：这正是「只按发生时刻排序」会翻车的形状——同一页边界上的行
    先后不定，某一条会在两页里重复、另一条被跳过。排序补上主键倒序后，总序是确定的。
    """

    same_moment = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    written = [make_record(user_id=READER_ID, occurred_at=same_moment) for _ in range(25)]

    async def scenario() -> list[dict[str, Any]]:
        async with usage_database() as sessions:
            await add_records(sessions, written)
            app = build_app(OfflineUsageRuntime(sessions))
            collected: list[dict[str, Any]] = []
            offset = 0
            while True:
                response = await get_records(app, limit=7, offset=offset)
                assert response.status_code == 200
                body = response.json()
                collected.extend(body["items"])
                if not body["has_more"]:
                    break
                offset += 7
            return collected

    collected = run(scenario())

    assert [item["call_id"] for item in collected] == [str(record.call_id) for record in reversed(written)]
    assert len({item["call_id"] for item in collected}) == len(written)


def test_the_response_has_no_exact_total() -> None:
    """响应形状是「记录数组 + 是否还有下一页」，不带精确总数。"""

    async def scenario() -> dict[str, Any]:
        async with usage_database() as sessions:
            await add_records(sessions, [make_record(user_id=READER_ID)])
            response = await get_records(build_app(OfflineUsageRuntime(sessions)))
            return response.json()

    body = run(scenario())

    assert set(body) == {"items", "has_more"}


def test_the_last_page_reports_no_more() -> None:
    """最后一页 ``has_more`` 为假，倒数第二页为真。"""

    async def scenario() -> list[bool]:
        async with usage_database() as sessions:
            await add_records(sessions, [make_record(user_id=READER_ID) for _ in range(3)])
            app = build_app(OfflineUsageRuntime(sessions))
            first = (await get_records(app, limit=2, offset=0)).json()["has_more"]
            second = (await get_records(app, limit=2, offset=2)).json()["has_more"]
            return [first, second]

    assert run(scenario()) == [True, False]


# 3、筛选：时间范围闭开、模型精确匹配、两者可叠加。


def test_the_time_range_is_half_open() -> None:
    """起点含、终点不含：边界上的记录恰好只被算一次。"""

    day = datetime(2026, 3, 1, tzinfo=UTC)
    records = [
        make_record(user_id=READER_ID, occurred_at=day - timedelta(microseconds=1)),
        make_record(user_id=READER_ID, occurred_at=day),
        make_record(user_id=READER_ID, occurred_at=day + timedelta(hours=23, minutes=59)),
        make_record(user_id=READER_ID, occurred_at=day + timedelta(days=1)),
    ]

    async def scenario() -> list[str]:
        async with usage_database() as sessions:
            await add_records(sessions, records)
            response = await get_records(
                build_app(OfflineUsageRuntime(sessions)),
                start=day.isoformat(),
                end=(day + timedelta(days=1)).isoformat(),
            )
            assert response.status_code == 200
            return [item["call_id"] for item in response.json()["items"]]

    returned = run(scenario())

    assert set(returned) == {str(records[1].call_id), str(records[2].call_id)}


def test_the_model_filter_selects_one_model() -> None:
    """模型筛选只返回该模型的记录。"""

    async def scenario() -> list[str | None]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="model-a"),
                make_record(user_id=READER_ID, model_name="model-b"),
            ])
            response = await get_records(build_app(OfflineUsageRuntime(sessions)), model="model-a")
            assert response.status_code == 200
            return [item["model_name"] for item in response.json()["items"]]

    assert run(scenario()) == ["model-a"]


def test_both_filters_apply_together() -> None:
    """两个筛选可以同时使用，交出的记录必须同时满足。"""

    day = datetime(2026, 3, 1, tzinfo=UTC)

    async def scenario() -> list[str | None]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="model-a", occurred_at=day),
                make_record(user_id=READER_ID, model_name="model-a", occurred_at=day - timedelta(days=1)),
                make_record(user_id=READER_ID, model_name="model-b", occurred_at=day),
            ])
            response = await get_records(
                build_app(OfflineUsageRuntime(sessions)),
                model="model-a",
                start=day.isoformat(),
            )
            assert response.status_code == 200
            return [item["model_name"] for item in response.json()["items"]]

    assert len(run(scenario())) == 1


# 4、空结果是正常响应。


def test_an_empty_result_is_not_an_error() -> None:
    """没有任何记录时返回空数组与「没有下一页」，而不是错误。"""

    async def scenario() -> httpx.Response:
        async with usage_database() as sessions:
            return await get_records(build_app(OfflineUsageRuntime(sessions)))

    response = run(scenario())

    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False}


# 5、用量库不可用是 503，不是空列表。


def test_a_missing_usage_runtime_is_a_503() -> None:
    """用量库资源缺失时返回 503，不把「查不到」渲染成空列表。"""

    async def scenario() -> httpx.Response:
        # 属性在、值缺：与真装配里「用量库根本没装起来」同形。
        return await get_records(build_app(OfflineUsageRuntime()))

    response = run(scenario())

    assert response.status_code == 503
    assert response.json()["code"] == "usage_runtime_unavailable"


def test_a_failing_usage_database_is_a_503() -> None:
    """用量库报错时返回 503，前端看到的不是「这个月没花钱」。"""

    class BrokenUsageRuntime(OfflineUsageRuntime):
        """会话工厂拿到手就报错的用量库替身。"""

        def __init__(self) -> None:
            super().__init__(session_factory=_raise_sqlalchemy_error)

    async def scenario() -> httpx.Response:
        return await get_records(build_app(BrokenUsageRuntime()))

    response = run(scenario())

    assert response.status_code == 503
    assert response.json() == {
        "code": "usage_database_unavailable",
        "detail": "用量库当前不可用。",
        "retryable": True,
    }


def _raise_sqlalchemy_error() -> Any:
    """模拟「会话都开不出来」的库故障。"""

    raise SQLAlchemyError("用量库不可达")


# 6、汇总：与明细同口径。


async def all_detail_items(app: FastAPI, **params: Any) -> list[dict[str, Any]]:
    """把明细的所有页拼成一个列表（每页只取 2 条，逼出多页）。

    汇总必须等于**全部**记录之和，而不是当前页之和；逐页取完再相加是唯一能真正验证这一点的
    做法。
    """

    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = await send(
            app, "GET", "/usage/records", params={**params, "limit": 2, "offset": offset}
        )
        assert response.status_code == 200
        body = response.json()
        collected.extend(body["items"])
        if not body["has_more"]:
            return collected
        offset += 2


def test_the_summary_equals_the_detail_rows_added_up() -> None:
    """每组筛选下，汇总的四列与次数逐列等于明细全部记录之和。

    筛选维度逐个走一遍（不限、按模型、按时间、两维叠加），每次都从明细接口把所有页取回来再相加；
    这条同时证明了「汇总接受每一个明细支持的筛选维度」与「汇总不受分页影响」。
    """

    day = datetime(2026, 3, 1, tzinfo=UTC)
    records = [
        make_record(user_id=READER_ID, model_name="model-a", occurred_at=day, cached_tokens=6),
        make_record(user_id=READER_ID, model_name="model-a", occurred_at=day, cached_tokens=None),
        make_record(user_id=READER_ID, model_name="model-b", occurred_at=day - timedelta(days=1)),
        make_record(user_id=READER_ID, model_name="model-b", occurred_at=day, cached_tokens=0),
        make_record(user_id=SUPERUSER_ID, model_name="model-a", occurred_at=day),
    ]
    filters: list[dict[str, Any]] = [
        {},
        {"model": "model-a"},
        {"start": day.isoformat()},
        {"model": "model-b", "start": day.isoformat()},
    ]

    async def scenario() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        async with usage_database() as sessions:
            await add_records(sessions, records)
            app = build_app(OfflineUsageRuntime(sessions))
            collected = []
            for params in filters:
                summary = (await send(app, "GET", "/usage/summary", params=params)).json()
                collected.append((summary, await all_detail_items(app, **params)))
            return collected

    for summary, items in run(scenario()):
        assert summary["call_count"] == len(items)
        assert summary["input_tokens"] == sum(item["input_tokens"] for item in items)
        assert summary["output_tokens"] == sum(item["output_tokens"] for item in items)
        assert summary["total_tokens"] == sum(item["total_tokens"] for item in items)
        reported = [item["cached_tokens"] for item in items if item["cached_tokens"] is not None]
        assert summary["cached_tokens"] == sum(reported)


def test_the_cached_total_skips_rows_that_never_reported_cache() -> None:
    """缓存那列只累加上游报了缓存的行；一行都没报时是 null，而不是 0。

    后一半是这条规则唯一可证伪的地方：把缺失当成 0 加进去时，总和与 0 无法区分，「没报缓存」
    就与「报了 0 缓存」混成了一件事。
    """

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="mixed", cached_tokens=6),
                make_record(user_id=READER_ID, model_name="mixed", cached_tokens=None),
                make_record(user_id=READER_ID, model_name="mixed", cached_tokens=9),
                make_record(user_id=READER_ID, model_name="silent", cached_tokens=None),
                make_record(user_id=READER_ID, model_name="silent", cached_tokens=None),
            ])
            app = build_app(OfflineUsageRuntime(sessions))
            mixed = (await send(app, "GET", "/usage/summary", params={"model": "mixed"})).json()
            silent = (await send(app, "GET", "/usage/summary", params={"model": "silent"})).json()
            return mixed, silent

    mixed, silent = run(scenario())

    assert mixed["cached_tokens"] == 15
    assert silent["cached_tokens"] is None
    assert silent["call_count"] == 2, "没报缓存不等于没发生调用"


def test_an_empty_range_sums_to_zero() -> None:
    """没有任何记录时返回四个 0 与次数 0，而不是错误。"""

    async def scenario() -> httpx.Response:
        async with usage_database() as sessions:
            app = build_app(OfflineUsageRuntime(sessions))
            return await send(app, "GET", "/usage/summary")

    response = run(scenario())

    assert response.status_code == 200
    assert response.json() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "call_count": 0,
    }


def test_another_accounts_calls_are_not_summed() -> None:
    """汇总与明细一样只看当前账号：别人的消耗不进自己的数字。"""

    async def scenario() -> dict[str, Any]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, input_tokens=3),
                make_record(user_id=SUPERUSER_ID, input_tokens=1000),
            ])
            app = build_app(OfflineUsageRuntime(sessions))
            return (await send(app, "GET", "/usage/summary")).json()

    summary = run(scenario())

    assert summary["call_count"] == 1
    assert summary["input_tokens"] == 3


def test_a_missing_usage_runtime_makes_the_summary_a_503() -> None:
    """用量库不可用时汇总也是 503，不返回零汇总。"""

    async def scenario() -> httpx.Response:
        return await send(build_app(OfflineUsageRuntime()), "GET", "/usage/summary")

    response = run(scenario())

    assert response.status_code == 503
    assert response.json()["code"] == "usage_runtime_unavailable"


def test_the_model_list_only_shows_the_current_accounts_models() -> None:
    """模型名列表只包含当前账号实际用过的模型，去重、排序，不含别人的。"""

    async def scenario() -> list[str]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="model-b"),
                make_record(user_id=READER_ID, model_name="model-a"),
                make_record(user_id=READER_ID, model_name="model-a"),
                make_record(user_id=SUPERUSER_ID, model_name="somebody-elses"),
            ])
            response = await send(build_app(OfflineUsageRuntime(sessions)), "GET", "/usage/models")
            assert response.status_code == 200
            return response.json()

    assert run(scenario()) == ["model-a", "model-b"]


def test_the_model_list_is_empty_when_nothing_was_used() -> None:
    """没有记录时模型名列表是空数组，而不是错误。"""

    async def scenario() -> httpx.Response:
        async with usage_database() as sessions:
            return await send(build_app(OfflineUsageRuntime(sessions)), "GET", "/usage/models")

    response = run(scenario())

    assert response.status_code == 200
    assert response.json() == []


def test_the_model_list_ignores_the_time_range() -> None:
    """模型名列表不跟时间范围走：否则选好的模型会在改范围后从选项里消失。"""

    day = datetime(2026, 3, 1, tzinfo=UTC)

    async def scenario() -> list[str]:
        async with usage_database() as sessions:
            await add_records(sessions, [
                make_record(user_id=READER_ID, model_name="old-model", occurred_at=day - timedelta(days=10)),
                make_record(user_id=READER_ID, model_name="new-model", occurred_at=day),
            ])
            app = build_app(OfflineUsageRuntime(sessions))
            response = await send(app, "GET", "/usage/models", params={"start": day.isoformat()})
            assert response.status_code == 200
            return response.json()

    assert run(scenario()) == ["new-model", "old-model"]


# 7、分页参数越界在契约层被挡下。


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"limit": 0}, 422),
        ({"limit": 10_000}, 422),
        ({"offset": -1}, 422),
    ],
)
def test_invalid_paging_parameters_are_rejected(params: dict[str, Any], expected_status: int) -> None:
    """越界的分页参数在契约层就被挡下，不会透到 SQL。"""

    async def scenario() -> httpx.Response:
        async with usage_database() as sessions:
            return await get_records(build_app(OfflineUsageRuntime(sessions)), **params)

    assert run(scenario()).status_code == expected_status
