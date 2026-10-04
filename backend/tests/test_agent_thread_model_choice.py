"""会话里选模型：记住、逐轮可见、失效时如实提示（HTTP 层，完全离线）。

这一组钉的是工单 04 里能在 HTTP 层观察到的那些勾选项，替身只换在三个边界上：模型、会话归属、
目录解析。路由、错误映射、快照与回放翻译都是真实代码。

**最要紧的两条**：

1. **失败发生在开始运行之前**——模型一次都不能被调用，新建会话那一行也不能落下。只断言状态码
   不够：一个「先跑了再报错」的实现同样能返回 409。
2. **只校验当轮生效的那一份**——请求里给了就用请求的，没给才用会话里存的；另一份失效不影响
   这一轮。

不连 PostgreSQL、Qdrant，不访问网络，也不调真实大模型。
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage

from agent_lab.agent.runs import AgentRunRegistry
from agent_lab.api.dependencies import get_llm_model_selection_service
from agent_lab.schemas.llm_models import ResolvedLlmModel
from tests.agent_helpers import ScriptedChatModel
from tests.app_helpers import (
    InMemoryLlmModelSelectionService,
    OfflineCatalogModel,
    create_agent_app,
    seed_owned_thread,
)

# 目录里那几条。id 固定，方便在断言里直接认。
DEFAULT_MODEL = UUID("40000000-0000-4000-8000-0000000000a1")
SECOND_MODEL = UUID("40000000-0000-4000-8000-0000000000b1")
STOPPED_MODEL = UUID("40000000-0000-4000-8000-0000000000c1")
UNKNOWN_MODEL = UUID("40000000-0000-4000-8000-0000000000d1")


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def catalog(*extra: OfflineCatalogModel, default_display_name: str = "默认模型"):
    """造一份目录：一条可用的默认模型 + 调用方给的其它条目。"""

    return InMemoryLlmModelSelectionService(
        [
            OfflineCatalogModel(DEFAULT_MODEL, display_name=default_display_name, is_default=True),
            *extra,
        ]
    )


def second_model(**overrides: Any) -> OfflineCatalogModel:
    """目录里的第二条可用模型（默认名字「备用模型」）。"""

    return OfflineCatalogModel(SECOND_MODEL, display_name="备用模型", **overrides)


def app_for(model: Any, models: InMemoryLlmModelSelectionService) -> tuple[FastAPI, Any]:
    """建离线应用并把它那条目录依赖换成给定替身。"""

    app, search = create_agent_app(model)
    app.state.offline_llm_models = models
    app.dependency_overrides[get_llm_model_selection_service] = lambda: models
    return app, search


async def within_lifespan(
    app: FastAPI,
    work: Callable[[httpx.AsyncClient], Awaitable[Any]],
) -> Any:
    """在同一个 lifespan 内跑完 ``work`` 里的全部请求。

    两次进 lifespan 会各装一个全新的 ``InMemorySaver``，「先选模型、再提问、再回放」这类
    用例必须待在一个里面才成立。
    """

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await work(client)


def events_of(response: httpx.Response) -> list[dict[str, Any]]:
    """把 SSE 响应体按帧解析成 JSON 对象。"""

    return [
        json.loads(frame.removeprefix("data: "))
        for frame in response.text.split("\n\n")
        if frame.startswith("data: ")
    ]


async def ask(
    client: httpx.AsyncClient, **payload: Any
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    """发一次提问并返回 ``(响应, 事件)``；在流开始之前失败时事件为空。"""

    response = await client.post("/agent/chat", json=payload)
    return response, events_of(response)


async def replay(client: httpx.AsyncClient, thread_id: UUID | str) -> dict[str, Any]:
    """读回放。"""

    response = await client.get(f"/agent/threads/{thread_id}/messages")
    assert response.status_code == 200
    return response.json()


def test_a_chosen_model_carries_over_to_the_next_question() -> None:
    """会话里选一个模型之后，下一次提问沿用同一个（会话行上那份就是当轮的退路）。"""

    models = catalog(second_model())
    model = ScriptedChatModel(responses=[AIMessage(content="第一答"), AIMessage(content="第二答")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        first_response, first = await ask(client, message="第一问")
        assert first_response.status_code == 200
        thread_id = first[0]["thread_id"]

        saved = await client.patch(
            f"/agent/threads/{thread_id}/model", json={"llm_model_id": str(SECOND_MODEL)}
        )
        assert saved.status_code == 200
        assert saved.json() == {"llm_model_id": str(SECOND_MODEL)}

        second_response, second = await ask(client, message="第二问", thread_id=thread_id)
        assert second_response.status_code == 200
        assert second[0]["thread_id"] == thread_id

        history = await replay(client, thread_id)
        assert history["llm_model"] == {"id": str(SECOND_MODEL), "display_name": "备用模型"}
        # 逐轮：第一轮用默认那个，第二轮用选中的那个。
        assert [turn["llm_model"]["display_name"] for turn in history["turns"]] == [
            "默认模型",
            "备用模型",
        ]
        # 解析确实发生在运行之前，而且第二次请求不带模型 id 时读的是会话行上那份。
        assert models.resolved == [None, SECOND_MODEL]

    run(within_lifespan(app, verify))
    assert model.call_count == 2


def test_a_model_in_the_request_is_written_back_to_the_thread_row() -> None:
    """请求里临时带上另一个模型：这一轮用它，而且会话行跟着改（改了就是记住）。"""

    models = catalog(second_model())
    model = ScriptedChatModel(responses=[AIMessage(content="答")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        # 新建会话时直接带上模型：新行的 llm_model_id 就是它，不需要先建再改。
        response, events = await ask(client, message="问", llm_model_id=str(SECOND_MODEL))
        assert response.status_code == 200
        thread_id = events[0]["thread_id"]
        assert models.resolved == [SECOND_MODEL]

        history = await replay(client, thread_id)
        assert history["llm_model"]["id"] == str(SECOND_MODEL)
        assert history["turns"][0]["llm_model"]["display_name"] == "备用模型"

    run(within_lifespan(app, verify))


def test_changing_the_model_while_a_run_is_in_flight_is_saved_anyway() -> None:
    """运行中改选照样保存：这条路由不看在途运行、也不碰停止请求。"""

    models = catalog(second_model())
    model = ScriptedChatModel(responses=[AIMessage(content="答")])
    app, _search = app_for(model, models)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id, active_run_id=uuid4())

    async def verify(client: httpx.AsyncClient) -> None:
        response = await client.patch(
            f"/agent/threads/{thread_id}/model", json={"llm_model_id": str(SECOND_MODEL)}
        )
        assert response.status_code == 200

    run(within_lifespan(app, verify))
    record = app.state.offline_threads.threads[thread_id]
    assert record.llm_model_id == SECOND_MODEL
    # 在途运行与它的停止请求都不受影响：改选只影响下一次运行。
    assert record.active_run_id is not None
    assert record.stop_requested_at is None


def test_selecting_an_unavailable_model_is_still_saved() -> None:
    """存一个当前已失效的选择**仍然成功**：可用性只在运行前那道门判，保存时不判。"""

    models = catalog(OfflineCatalogModel(STOPPED_MODEL, display_name="停用模型", enabled=False))
    model = ScriptedChatModel(responses=[AIMessage(content="答")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        _, events = await ask(client, message="第一问")
        thread_id = events[0]["thread_id"]

        response = await client.patch(
            f"/agent/threads/{thread_id}/model", json={"llm_model_id": str(STOPPED_MODEL)}
        )

        assert response.status_code == 200
        assert response.json() == {"llm_model_id": str(STOPPED_MODEL)}
        # 保存成功之外，界面还得说得出「原来是 xxx」——停用的条目照样读得到名字，
        # 而且**不静默回落**成默认模型。
        history = await replay(client, thread_id)
        assert history["llm_model"] == {"id": str(STOPPED_MODEL), "display_name": "停用模型"}
        # 已经发生过的那一轮不受影响：它用的是当时那个默认模型。
        assert history["turns"][0]["llm_model"]["id"] == str(DEFAULT_MODEL)

    run(within_lifespan(app, verify))


def test_selecting_a_missing_model_id_is_a_404_before_the_run_starts() -> None:
    """把选择指向一个不存在的 id：提问在开始运行之前失败，404。"""

    models = catalog()
    model = ScriptedChatModel(responses=[AIMessage(content="不该被调用")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        response, _ = await ask(client, message="问", llm_model_id=str(UNKNOWN_MODEL))

        assert response.status_code == 404
        assert response.json() == {
            "code": "llm_model_entry_not_found",
            "detail": "该可用模型不存在，请刷新列表后重试。",
            "retryable": False,
        }
        # 失败发生在运行之前：模型一次都没被调用，也没有落下一条开不起来的会话。
        assert model.call_count == 0
        assert app.state.offline_threads.threads == {}

    run(within_lifespan(app, verify))


def test_an_unavailable_effective_model_is_a_409_without_calling_the_model() -> None:
    """生效的那一个已停用（含所属渠道停用）：提问在开始运行之前失败，409。"""

    models = catalog(
        OfflineCatalogModel(STOPPED_MODEL, display_name="渠道停了的模型", provider_enabled=False)
    )
    model = ScriptedChatModel(responses=[AIMessage(content="不该被调用")])
    app, _search = app_for(model, models)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id, llm_model_id=STOPPED_MODEL)

    async def verify(client: httpx.AsyncClient) -> None:
        response, _ = await ask(client, message="问", thread_id=str(thread_id))

        assert response.status_code == 409
        assert response.json() == {
            "code": "llm_model_unavailable",
            "detail": "选中的模型当前不可用，请换一个模型再提问。",
            "retryable": False,
        }
        assert model.call_count == 0
        # 会话行上的选择一字未改，而且占位没被拿走：用户换一个模型就能立刻再问。
        record = app.state.offline_threads.threads[thread_id]
        assert record.llm_model_id == STOPPED_MODEL
        assert record.active_run_id is None

    run(within_lifespan(app, verify))


def test_an_empty_catalog_is_its_own_error_and_does_not_fall_back() -> None:
    """目录为空或全部停用：另一条文案、另一个 code，而且不回头读环境变量兜底。"""

    models = InMemoryLlmModelSelectionService([])
    model = ScriptedChatModel(responses=[AIMessage(content="不该被调用")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        response, _ = await ask(client, message="问")

        assert response.status_code == 409
        assert response.json() == {
            "code": "no_available_llm_models",
            "detail": "当前没有可用的模型，请联系管理员配置。",
            "retryable": False,
        }
        assert model.call_count == 0

    run(within_lifespan(app, verify))


def test_only_the_effective_selection_is_checked() -> None:
    """只校验当轮生效的那一份：会话行上是个失效选择、而请求带了可用的，这一轮照常跑。"""

    models = catalog(
        second_model(),
        OfflineCatalogModel(STOPPED_MODEL, display_name="停用模型", enabled=False),
    )
    model = ScriptedChatModel(responses=[AIMessage(content="答")])
    app, _search = app_for(model, models)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id, llm_model_id=STOPPED_MODEL)

    async def verify(client: httpx.AsyncClient) -> None:
        response, events = await ask(
            client, message="问", thread_id=str(thread_id), llm_model_id=str(SECOND_MODEL)
        )

        assert response.status_code == 200
        assert events[-1]["event"] == "done"
        assert models.resolved == [SECOND_MODEL]
        # 请求里带的那份写回会话行：换掉的就是那个失效的选择。
        assert app.state.offline_threads.threads[thread_id].llm_model_id == SECOND_MODEL

    run(within_lifespan(app, verify))
    assert model.call_count == 1


def test_a_turn_keeps_the_name_it_ran_with_when_the_entry_is_later_renamed() -> None:
    """条目后来改名或停用，都不改写已经发生过的那几轮——名字是当时的快照。"""

    models = catalog(second_model())
    model = ScriptedChatModel(responses=[AIMessage(content="第一答"), AIMessage(content="第二答")])
    app, _search = app_for(model, models)

    async def verify(client: httpx.AsyncClient) -> None:
        _, first = await ask(client, message="第一问")
        thread_id = first[0]["thread_id"]
        await client.patch(
            f"/agent/threads/{thread_id}/model", json={"llm_model_id": str(SECOND_MODEL)}
        )
        await ask(client, message="第二问", thread_id=thread_id)

        # 管理员事后改名并停用它。
        models.find(SECOND_MODEL).display_name = "改过的名字"
        models.find(SECOND_MODEL).enabled = False

        history = await replay(client, thread_id)

        assert [turn["llm_model"]["display_name"] for turn in history["turns"]] == [
            "默认模型",
            "备用模型",
        ]
        # 会话**当前**选择读的是此刻的目录：名字是新的（界面据此显示「原来是改过的名字」）。
        assert history["llm_model"] == {"id": str(SECOND_MODEL), "display_name": "改过的名字"}

    run(within_lifespan(app, verify))


def test_a_rebuilt_run_context_reads_the_frozen_model_instead_of_the_thread_row() -> None:
    """接手重建上下文读的是提问消息上冻结的那一份，不是会话行上的当前选择。

    走的是接手者真正用的那个读取器：冻结的口袋与回放是同一个（提问消息的 ``agent_run``），
    所以接手那一轮用的模型与回看显示的是同一个。它只读冻结值，**不重查目录、也不重判可用性**
    ——快照里的模型在排空期间被停用时仍用它跑完。
    """

    thread_id = uuid4()
    run_id = uuid4()
    frozen = ResolvedLlmModel(id=SECOND_MODEL, display_name="备用模型", context_window=8192)
    message = HumanMessage(
        content="问",
        additional_kwargs={
            "agent_run": {
                "run_id": str(run_id),
                "llm_model": frozen.model_dump(mode="json"),
            }
        },
    )

    class ReadOnlyGraph:
        """只实现读取器要用的那一个方法。"""

        async def aget_state(self, _config: Any) -> Any:
            return SimpleNamespace(values={"messages": [message]})

    found, scope, model = run(
        AgentRunRegistry._read_frozen_run_meta(ReadOnlyGraph(), thread_id=thread_id, run_id=run_id)
    )

    assert (found, scope) == (True, None)
    assert model == frozen
