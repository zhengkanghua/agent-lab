"""运行范围、真实 Tool 证据与终态一致性；假模型和内存 checkpointer，不连接上游。"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from agent_lab.agent.context import AgentContext
from agent_lab.agent.evidence import DocumentEvidence, ToolEvidence, resolve_citations, tool_evidence
from agent_lab.agent.replay import build_replay_turns
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.agent.tools.read_document import build_read_document_tool
from agent_lab.agent.tools.search_documents import build_search_documents_tool
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.domain import KnowledgeBase, KnowledgeBaseStorageError
from agent_lab.knowledge.scope import KnowledgeBaseSummary, ResolvedKnowledgeBaseScope
from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS, CountingTool, ScriptedChatModel, StreamingChatModel,
    build_offline_graph, parallel_tool_call_message, run, tool_call_message,
)
from tests.agent_scope_helpers import NEWS_SCOPE, invoke_tool_message
from tests.app_helpers import create_agent_app, seed_owned_thread
from tests.test_agent_threads_api import within_lifespan
from tests.test_agent_tools import FakeSearchService, FakeSessionFactory, build_record, build_result


FILE_BASE = KnowledgeBaseSummary(id=uuid4(), key="manuals", name="操作资料", description="操作手册")
FILE_SCOPE = ResolvedKnowledgeBaseScope(mode="selected", knowledge_bases=(FILE_BASE,))
ALL_SCOPE = ResolvedKnowledgeBaseScope(mode="all", knowledge_bases=(*NEWS_SCOPE.knowledge_bases, FILE_BASE))


def evidence(**changes):
    values = dict(document_id=uuid4(), knowledge_base_id=NEWS_SCOPE.knowledge_base_ids[0],
                  knowledge_base_name="新闻", title="运行手册", content_hash="a" * 64,
                  excerpt="备份保留 7 天。", kind="match")
    return DocumentEvidence(**(values | changes))


def question(text, context):
    return HumanMessage(content=text, additional_kwargs={"agent_run": {
        "run_id": str(context.run_id), "scope": context.scope.model_dump(mode="json"),
    }})


async def run_and_replay(graph, *, context=None, thread_id=None, message="问题"):
    context = context or AgentContext(scope=NEWS_SCOPE)
    thread_id = thread_id or uuid4()
    events = [event async for event in stream_agent_events(
        graph, message=message, thread_id=thread_id, context=context,
        langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
    )]
    snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    turns, summarized, summary = build_replay_turns(snapshot.values["messages"])
    return events, turns, summarized, summary


@pytest.mark.parametrize("ids", [None, [FILE_BASE.id]])
def test_search_uses_snapshot_or_explicit_narrowing(ids):
    service = FakeSearchService([])
    arguments = {"query": "备份"}
    if ids is not None:
        arguments["knowledge_base_ids"] = ids
    output = run(invoke_tool_message(build_search_documents_tool(service), arguments, context=AgentContext(scope=ALL_SCOPE)))
    artifact = ToolEvidence.model_validate(output.artifact)
    expected = ALL_SCOPE.knowledge_base_ids if ids is None else tuple(ids)
    assert service.requests[0].filters.knowledge_base_ids == expected
    assert artifact.scope.knowledge_base_ids == expected
    assert "没有检索到" in output.content


@pytest.mark.parametrize("ids", [[], [uuid4()], [*NEWS_SCOPE.knowledge_base_ids, FILE_BASE.id]])
def test_search_cannot_expand_selected_scope_or_remove_filter(ids):
    service = FakeSearchService([])
    result = run(invoke_tool_message(build_search_documents_tool(service), {"query": "备份", "knowledge_base_ids": ids}))
    assert result.status == "error" and result.artifact is None
    assert not service.requests


def test_search_evidence_is_the_actual_indexed_version_and_fragment():
    hit = build_result(additional=1)
    context = AgentContext(scope=NEWS_SCOPE)
    message = run(invoke_tool_message(build_search_documents_tool(FakeSearchService([hit])), {"query": "利率"}, context=context))
    artifact = tool_evidence(message, run_id=context.run_id, scope=context.scope)
    assert artifact is not None
    assert [item.excerpt for item in artifact.evidence] == [hit.best_match.page_content, hit.additional_matches[0].page_content]
    assert all(item.content_hash == hit.content_hash and item.document_id == hit.document_id for item in artifact.evidence)
    assert len({item.citation_id for item in artifact.evidence}) == 2


def test_actual_tools_keep_two_versions_of_evidence_consistent_in_sse_and_replay():
    class CitingModel(ScriptedChatModel):
        def _generate(self, messages, *args, **kwargs):
            retrieved = [message for message in messages if isinstance(message, ToolMessage)]
            if len(retrieved) == 2:
                ids = [message.artifact["evidence"][0]["citation_id"] for message in retrieved]
                self.responses = [AIMessage(content=f"旧片段 [[{ids[0]}]]；当前正文 [[{ids[1]}]]")]
                self.i = 0
            return super()._generate(messages, *args, **kwargs)

    hit = build_result()
    record = build_record(content_text="当前正文与旧命中不同")
    record.content_hash = "b" * 64
    model = CitingModel(responses=[tool_call_message("search_documents", {"query": "利率"}),
                                  tool_call_message("read_document", {"document_id": str(record.id)})])
    graph = build_offline_graph(model, [build_search_documents_tool(FakeSearchService([hit])), build_read_document_tool(FakeSessionFactory(record))])
    events, turns, _, _ = run(run_and_replay(graph))
    assert events[-1].event == "done" and events[-1].status == "completed"
    assert events[-1].citations == turns[-1].citations
    assert [item.content_hash for item in events[-1].citations] == ["a" * 64, "b" * 64]
    assert len({item.citation_id for item in events[-1].citations}) == 2
    assert all(event.scope == NEWS_SCOPE for event in events if event.event == "tool_result")


def test_read_source_less_file_uses_current_hash_and_new_citation():
    record = build_record(content_text="# 当前资料\n\n备份保留 14 天。")
    record.source = None
    record.current_version.metadata_snapshot = {"source_name": None}
    record.url = record.published_at = None
    record.upload_filename = "手册.md"
    record.content_hash = "b" * 64
    message = run(invoke_tool_message(build_read_document_tool(FakeSessionFactory(record)), {"document_id": record.id}))
    artifact = ToolEvidence.model_validate(message.artifact)
    current = artifact.evidence[0]
    assert current.kind == "document" and current.content_hash == "b" * 64
    assert current.excerpt == record.content_text
    assert current.source_name is None and current.upload_filename == "手册.md"
    assert "None" not in message.content


@pytest.mark.parametrize("reason", ["outside", "inactive"])
def test_known_document_id_cannot_bypass_scope_or_enabled_state(reason):
    record = build_record(content_text="不能泄露的正文")
    if reason == "outside":
        record.knowledge_base_id = FILE_BASE.id
    else:
        record.knowledge_base.is_active = False
    message = run(invoke_tool_message(build_read_document_tool(FakeSessionFactory(record)), {"document_id": record.id}))
    assert message.status == "error" and message.artifact is None
    assert record.content_text not in message.content


@pytest.mark.parametrize("change", ["old_run", "outside_scope", "mismatched_document", "failed"])
def test_artifact_cannot_claim_old_or_out_of_scope_evidence(change):
    context = AgentContext(scope=NEWS_SCOPE)
    item = evidence(knowledge_base_id=FILE_BASE.id) if change == "mismatched_document" else evidence()
    artifact = ToolEvidence(run_id=uuid4() if change == "old_run" else context.run_id,
                            scope=ALL_SCOPE if change == "outside_scope" else NEWS_SCOPE, evidence=(item,))
    message = ToolMessage(content="资料", tool_call_id="call", artifact=artifact.model_dump(mode="json"),
                          status="error" if change == "failed" else "success")
    assert tool_evidence(message, run_id=context.run_id, scope=context.scope) is None


def test_only_actual_identifiers_resolve_and_two_document_versions_stay_distinct():
    old = evidence()
    current = evidence(document_id=old.document_id, content_hash="b" * 64, excerpt="现在保留 14 天。", kind="document")
    answer = f"旧资料 [[{old.citation_id}]]；当前资料 [[{current.citation_id}]]；伪造 [[Effffffffffff]]"
    citations, invalid = resolve_citations(answer, [old, current])
    assert citations == (old, current)
    assert invalid == ("Effffffffffff",)
    assert citations[0].document_id == citations[1].document_id
    assert citations[0].content_hash != citations[1].content_hash


def test_repeated_call_ids_in_later_turn_do_not_rewrite_old_trace():
    messages = [HumanMessage(content="第一问"), tool_call_message("search_documents", {"query": "甲"}),
                ToolMessage(content="甲的资料", tool_call_id="call-search_documents"), AIMessage(content="答甲"),
                HumanMessage(content="第二问"), tool_call_message("search_documents", {"query": "乙"}),
                ToolMessage(content="乙的资料", tool_call_id="call-search_documents"), AIMessage(content="答乙")]
    turns, _, _ = build_replay_turns(messages)
    assert [turn.traces[0].content for turn in turns] == ["甲的资料", "乙的资料"]


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter"])
def test_truncated_answer_is_incomplete_in_stream_and_replay(reason):
    model = ScriptedChatModel(responses=[AIMessage(content="回答到一半", response_metadata={"finish_reason": reason})])
    events, turns, _, _ = run(run_and_replay(build_offline_graph(model)))
    assert events[-1].event == "done"
    assert events[-1].status == turns[-1].status == "incomplete"
    assert events[-1].answer == turns[-1].answer == "回答到一半"


def test_model_budget_exhaustion_is_incomplete_in_stream_and_replay():
    counter = CountingTool("lookup")
    model = ScriptedChatModel(responses=[tool_call_message("lookup", {"text": "继续查"})])
    events, turns, _, _ = run(run_and_replay(build_offline_graph(model, [counter.build()])))
    assert events[-1].event == "done"
    assert events[-1].status == turns[-1].status == "incomplete"
    assert events[-1].answer == turns[-1].answer
    assert counter.invocations


class RetryAfterTextModel(StreamingChatModel):
    """首次已流出文本才断线，重试返回不同答案，模拟真实 provider 的失败窗口。"""

    attempts: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            yield ChatGenerationChunk(message=AIMessageChunk(content="discarded"))
            raise ConnectionError("offline retry")
        yield ChatGenerationChunk(message=AIMessageChunk(content="final"))


def test_successful_retry_final_text_matches_checkpoint_not_discarded_tokens():
    model = RetryAfterTextModel(messages=iter([]))
    events, turns, _, _ = run(run_and_replay(build_offline_graph(model)))
    assert "discarded" in "".join(event.text for event in events if event.event == "token")
    assert events[-1].answer == turns[-1].answer == "final"
    assert events[-1].status == turns[-1].status == "completed"


def test_scope_switch_removes_old_answers_tools_and_summary_from_model_input():
    model = ScriptedChatModel(responses=[AIMessage(content="当前范围资料不足 [[Effffffffffff]]")])
    graph = build_offline_graph(model)
    thread_id = uuid4()
    previous = AgentContext(scope=NEWS_SCOPE)
    messages = [HumanMessage(content="摘要中的旧秘密", additional_kwargs={"lc_source": "summarization"}),
                question("第一问", previous), tool_call_message("search_documents", {"query": "旧查询"}),
                ToolMessage(content="旧工具秘密", tool_call_id="call-search_documents"), AIMessage(content="旧回答秘密")]

    async def verify():
        await graph.aupdate_state({"configurable": {"thread_id": str(thread_id)}}, {"messages": messages})
        return await run_and_replay(graph, context=AgentContext(scope=FILE_SCOPE, system_prompt="自定义风格"), thread_id=thread_id)

    events, turns, _, _ = run(verify())
    received = "\n".join(message.text for message in model.received_messages[-1])
    assert "第一问" in received and FILE_BASE.name in received and "自定义风格" in received
    assert all(text not in received for text in ["摘要中的旧秘密", "旧工具秘密", "旧回答秘密"])
    assert events[-1].citations == turns[-1].citations == ()
    assert events[-1].invalid_citations == turns[-1].invalid_citations == ("Effffffffffff",)


def test_real_summarization_keeps_recent_long_turn_whole_and_current_evidence_valid():
    # 旧 20 条 + 近期一整段 21 条 + 新提问越过 40；原按条数切会删掉近期提问。
    history = [message for index in range(10) for message in (HumanMessage(content=f"旧问{index}"), AIMessage(content=f"旧答{index}"))]
    context = AgentContext(scope=NEWS_SCOPE)
    item = evidence()
    history.append(question("近期完整提问", context))
    for index in range(7):
        calls = [(f"c-{index}-{part}", "lookup", {"text": "资料"}) for part in range(2 if index < 5 else 1)]
        history.append(parallel_tool_call_message(calls))
        history.extend(ToolMessage(content="近期资料", tool_call_id=call_id,
            artifact=ToolEvidence(run_id=context.run_id, scope=NEWS_SCOPE, evidence=(item,)).model_dump(mode="json"))
            for call_id, _, _ in calls)
    history.append(AIMessage(content=f"近期答案 [[{item.citation_id}]]", additional_kwargs={"agent_run": {"run_id": str(context.run_id), "completed": True}}))
    assert len(history) == 41
    model = ScriptedChatModel(responses=[AIMessage(content="旧背景摘要"), AIMessage(content="当前答案")])
    graph = build_offline_graph(model)
    thread_id = uuid4()

    async def verify():
        await graph.aupdate_state({"configurable": {"thread_id": str(thread_id)}}, {"messages": history})
        return await run_and_replay(graph, thread_id=thread_id, message="当前提问")

    _, turns, summarized, summary = run(verify())
    assert summarized and summary and "旧背景摘要" in summary
    assert [turn.question for turn in turns] == ["近期完整提问", "当前提问"]
    assert turns[0].citations == (item,) and turns[0].status == "completed"
    assert all("近期资料" not in message.text for message in model.received_messages[-1])


def sse(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def catalog_service(records):
    service = KnowledgeBaseService(None)
    service.list = AsyncMock(side_effect=lambda include_inactive=False: [item for item in records if include_inactive or item.is_active])
    return service


def base(summary, active=True):
    return KnowledgeBase(summary.id, summary.key, summary.name, summary.description, active, datetime.now(UTC), datetime.now(UTC))


def test_http_default_all_saved_selection_reopen_and_old_turn_scope():
    app, runtime = create_agent_app(ScriptedChatModel(responses=[AIMessage(content="答")]))
    disabled = base(KnowledgeBaseSummary(id=uuid4(), key="disabled", name="停用"), False)
    catalog = catalog_service([base(NEWS_SCOPE.knowledge_bases[0]), base(FILE_BASE), disabled])
    runtime.service.resolve_scope = catalog.resolve_scope

    async def verify(client):
        first = await client.post("/agent/chat", json={"message": "第一问"})
        assert first.status_code == 200
        started = sse(first)[0]
        assert started["scope"]["mode"] == "all"
        assert {item["id"] for item in started["scope"]["knowledge_bases"]} == {str(value) for value in ALL_SCOPE.knowledge_base_ids}
        thread_id = started["thread_id"]
        selection = {"mode": "selected", "knowledge_base_ids": [str(FILE_BASE.id)]}
        saved = await client.patch(f"/agent/threads/{thread_id}/scope", json=selection)
        assert saved.status_code == 200
        second = await client.post("/agent/chat", json={"message": "第二问", "thread_id": thread_id})
        assert [item["id"] for item in sse(second)[0]["scope"]["knowledge_bases"]] == [str(FILE_BASE.id)]
        replay = (await client.get(f"/agent/threads/{thread_id}/messages")).json()
        assert replay["scope"] == selection
        assert [turn["scope"]["mode"] for turn in replay["turns"]] == ["all", "selected"]
        foreign = uuid4()
        seed_owned_thread(app, foreign, user_id=uuid4())
        assert (await client.patch(f"/agent/threads/{foreign}/scope", json=selection)).status_code == 404
    run(within_lifespan(app, verify))


@pytest.mark.parametrize("failure,status", [("empty", 409), ("inactive", 409), ("missing", 404), ("unavailable", 503), ("empty_selection", 422)])
def test_http_invalid_scope_fails_before_creating_thread_or_calling_model(failure, status):
    model = ScriptedChatModel(responses=[AIMessage(content="不能调用")])
    app, runtime = create_agent_app(model)
    catalog = catalog_service([base(FILE_BASE, False)] if failure == "inactive" else [])
    if failure == "unavailable":
        catalog.list = AsyncMock(side_effect=KnowledgeBaseStorageError())
    runtime.service.resolve_scope = catalog.resolve_scope
    selection = {"mode": "all"} if failure in {"empty", "unavailable"} else {"mode": "selected", "knowledge_base_ids": [] if failure == "empty_selection" else [str(FILE_BASE.id)]}

    async def verify(client):
        response = await client.post("/agent/chat", json={"message": "问", "scope": selection})
        assert response.status_code == status
        assert model.call_count == 0 and not app.state.offline_threads.threads
    run(within_lifespan(app, verify))
