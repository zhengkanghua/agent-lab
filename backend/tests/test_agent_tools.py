"""Agent 只读工具的参数契约、输出格式和异常传播测试。

本文件的重点不是「工具能跑通」，而是三件容易出错、且出错后很难从现象反推的事：

1. **异常必须抛出去**：工具内部 catch 掉异常，重试和脱敏中间件就全部失效，而表面上
   一切正常——模型只会收到一句奇怪的文案。测试直接断言异常穿透工具边界。
2. **输出里必须有 document_id、且不能有 score**：前者是模型引用和后续 read_document
   的唯一依据，缺了模型就只能编；后者是原始 Cosine 值，给模型看会被说成「80% 相关」。
3. **Session 必须一次调用一开一关**：Agent 是进程级的，Session 不是。工具持有长命
   Session 会在并发下串数据。

替身只做在 Service 和 Session 这两个边界上：``DocumentRepository`` 用的是真实实现，
喂给它一个只实现了 ``scalar`` 的假 Session。这样「Repository 拿 UUID 查出的是不是这一行」
仍由真实代码决定，测试不必替 SQLAlchemy 编造行为。

默认测试全部离线：不访问 Ollama、Qdrant 或 PostgreSQL。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from agent_lab.agent.limits import (
    READ_DOCUMENT_MAX_CHARS,
    SEARCH_TOOL_MAX_DOCUMENTS,
    SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT,
)
from agent_lab.agent.tools import build_agent_tools
from agent_lab.agent.tools.read_document import build_read_document_tool
from agent_lab.agent.tools.search_news import (
    SearchNewsArguments,
    build_search_news_tool,
)
from agent_lab.models.document import DocumentRecord
from agent_lab.models.source import SourceRecord
from agent_lab.schemas.document_search import (
    DocumentSearchMatch,
    DocumentSearchResult,
)
from tests.agent_helpers import run


DOCUMENT_ID = UUID("11111111-1111-4111-8111-111111111111")
CONTENT_HASH = "a" * 64
PUBLISHED_AT = datetime(2026, 3, 5, 9, 30, tzinfo=UTC)


def build_result(*, additional: int = 0) -> DocumentSearchResult:
    """构造一条文档级检索结果，score 取一个明显好认的值便于断言它没被输出。"""

    def match(index: int, score: float) -> DocumentSearchMatch:
        return DocumentSearchMatch(
            chunk_id=uuid4(),
            score=score,
            page_content=f"第 {index} 段正文内容。",
            chunk_index=index,
            chunk_count=5,
        )

    return DocumentSearchResult(
        document_id=DOCUMENT_ID,
        content_hash=CONTENT_HASH,
        title="央行宣布降息",
        url="https://example.com/news/1",
        source_name="示例财经",
        published_at=PUBLISHED_AT,
        authors=["张三"],
        labels=["财经"],
        chunk_count=5,
        best_score=0.87654321,
        best_match=match(0, 0.87654321),
        additional_matches=[match(index, 0.5) for index in range(1, additional + 1)],
    )


def build_record(*, content_text: str) -> DocumentRecord:
    """构造一条已 eager-load ``source`` 的 ORM 文档。

    直接实例化 ORM 类而不用 ``SimpleNamespace``：字段名写错时前者立刻报错，后者会安静
    地让测试通过——而工具读的正是这些字段名。
    """

    record = DocumentRecord(
        id=DOCUMENT_ID,
        title="央行宣布降息",
        url="https://example.com/news/1",
        published_at=PUBLISHED_AT,
        authors=["张三"],
        content_text=content_text,
        content_hash=CONTENT_HASH,
    )
    record.source = SourceRecord(name="示例财经")
    return record


class FakeSearchService:
    """记录请求参数的假检索 Service，可预置返回值或异常。"""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[Any] = []

    async def search_documents(self, request: Any) -> list[DocumentSearchResult]:
        """返回预置结果，或抛出预置异常。"""

        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeSession:
    """只实现 ``scalar`` 的假 AsyncSession，够真实 Repository 用。"""

    def __init__(self, owner: "FakeSessionFactory") -> None:
        self.owner = owner

    async def scalar(self, _statement: Any) -> DocumentRecord | None:
        """返回工厂预置的记录，或抛出预置的数据库异常。"""

        if isinstance(self.owner.outcome, BaseException):
            raise self.owner.outcome
        return self.owner.outcome

    async def __aenter__(self) -> "FakeSession":
        self.owner.opened += 1
        return self

    async def __aexit__(self, *_args: Any) -> None:
        self.owner.closed += 1


class FakeSessionFactory:
    """每次调用产出一个新的假 Session，并统计开关次数。"""

    def __init__(self, outcome: Any = None) -> None:
        self.outcome = outcome
        self.opened = 0
        self.closed = 0

    def __call__(self) -> FakeSession:
        return FakeSession(self)


def test_search_tool_output_carries_document_id() -> None:
    """检索结果必须带 document_id，否则模型无法引用也无法读全文。"""

    service = FakeSearchService([build_result()])
    news_tool = build_search_news_tool(service)  # type: ignore[arg-type]

    output = run(news_tool.ainvoke({"query": "央行降息"}))

    assert str(DOCUMENT_ID) in output
    assert "央行宣布降息" in output
    assert "2026-03-05" in output


def test_search_tool_output_hides_the_raw_score() -> None:
    """检索结果不得包含原始 Cosine score。

    这不是安全问题而是正确性问题：0.876 这种数字给模型看，它极可能在回答里写成「相关度
    87%」——但 Cosine 相似度不是概率，那句话是编的。不给它这个数字最省事。
    """

    service = FakeSearchService([build_result(additional=2)])
    news_tool = build_search_news_tool(service)  # type: ignore[arg-type]

    output = run(news_tool.ainvoke({"query": "央行降息"}))

    assert "0.87" not in output
    assert "score" not in output.lower()


def test_search_tool_explains_an_empty_result() -> None:
    """没有命中时返回明确说明，不返回空字符串。

    空字符串会让模型以为工具坏了，接着重试或者干脆编一个答案。一句「没检索到，可能确实
    没有，也可能换个说法能找到」能引导它做正确的下一步。
    """

    service = FakeSearchService([])
    news_tool = build_search_news_tool(service)  # type: ignore[arg-type]

    output = run(news_tool.ainvoke({"query": "不存在的主题"}))

    assert output.strip()
    assert "没有检索到" in output


def test_search_tool_caps_matches_per_document() -> None:
    """每篇新闻的片段数用 Agent 侧上限，不用 HTTP 接口的默认值。

    两个上限的用途不同：HTTP 接口的结果给人看，可以多给几段让人自己扫；工具的结果要进
    模型上下文，每段都在花 token 并挤占后续对话空间。
    """

    service = FakeSearchService([build_result()])
    news_tool = build_search_news_tool(service)  # type: ignore[arg-type]

    run(news_tool.ainvoke({"query": "央行降息"}))

    assert service.requests[0].matches_per_document == SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT
    assert service.requests[0].document_limit == SEARCH_TOOL_MAX_DOCUMENTS


def test_search_tool_rejects_a_document_limit_over_the_cap() -> None:
    """超出上限的 document_limit 必须被参数校验拒绝。

    模型完全可能填 ``document_limit: 50``——它看不到我们的 token 预算。校验挡在 Service
    之前，所以这类错误不会白白消耗一次 Embedding 和一次 Qdrant 查询。
    """

    with pytest.raises(ValidationError):
        SearchNewsArguments(query="央行降息", document_limit=SEARCH_TOOL_MAX_DOCUMENTS + 1)


def test_search_tool_lets_upstream_errors_propagate() -> None:
    """上游异常必须穿透工具边界，交给中间件处理。

    这是本文件最重要的一条。工具里写个 ``except Exception: return "失败了"`` 看起来更
    「健壮」，实际效果是让 ToolRetryMiddleware 和 ToolErrorMiddleware 同时失效——重试
    永不触发、脱敏文案永不生效，而且没有任何报错提示你这件事发生了。
    """

    service = FakeSearchService(RuntimeError("Qdrant 连接失败"))
    news_tool = build_search_news_tool(service)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        run(news_tool.ainvoke({"query": "央行降息"}))


def test_read_tool_opens_and_closes_one_session_per_call() -> None:
    """每次调用开一个 Session，并在返回前关掉。

    Agent 是进程级共享的，Session 是一次工作单元。工具若持有长命 Session，并发请求会
    共用同一个事务，读到彼此未提交的状态。这条断言钉住「一次调用一个 Session」。
    """

    factory = FakeSessionFactory(build_record(content_text="正文。"))
    read_tool = build_read_document_tool(factory)  # type: ignore[arg-type]

    run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))
    run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))

    assert factory.opened == 2
    assert factory.closed == 2


def test_read_tool_explains_a_missing_document_instead_of_raising() -> None:
    """文档不存在是正常业务结果，返回说明而不是抛异常。

    抛异常的后果是：中间件重试三次（每次都查不到），然后模型收到一句「系统故障」——但
    真实情况是这个 id 不存在，模型该做的是重新检索。语义完全错了。
    """

    factory = FakeSessionFactory(None)
    read_tool = build_read_document_tool(factory)  # type: ignore[arg-type]

    output = run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))

    assert str(DOCUMENT_ID) in output
    assert "没有找到" in output


def test_read_tool_returns_metadata_with_the_body() -> None:
    """正文之外还要给出标题、来源和发布时间。

    模型引用时需要这些字段；缺了它只能写「某篇报道称」，那种引用无法核对。
    """

    factory = FakeSessionFactory(build_record(content_text="降息幅度为 25 个基点。"))
    read_tool = build_read_document_tool(factory)  # type: ignore[arg-type]

    output = run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))

    assert "央行宣布降息" in output
    assert "示例财经" in output
    assert "2026-03-05 09:30" in output
    assert "降息幅度为 25 个基点。" in output


def test_read_tool_truncates_an_overlong_body_with_a_visible_marker() -> None:
    """超长正文要截断，并且必须显式标注截断了。

    不标注的话模型会基于半篇文章下「文中没有提到 X」这类结论——而 X 可能就在后半篇。
    """

    long_body = "正" * (READ_DOCUMENT_MAX_CHARS + 500)
    factory = FakeSessionFactory(build_record(content_text=long_body))
    read_tool = build_read_document_tool(factory)  # type: ignore[arg-type]

    output = run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))

    assert "未读取" in output
    assert output.count("正") <= READ_DOCUMENT_MAX_CHARS + 10


def test_read_tool_lets_database_errors_propagate() -> None:
    """数据库故障必须抛出去，交给中间件重试和脱敏。

    与「文档不存在」正好相对：那个是业务结果，这个是真故障。区分它们决定了模型看到的是
    「换个检索词」还是「稍后再试」。
    """

    factory = FakeSessionFactory(
        OperationalError("SELECT 1", {}, Exception("connection refused"))
    )
    read_tool = build_read_document_tool(factory)  # type: ignore[arg-type]

    with pytest.raises(OperationalError):
        run(read_tool.ainvoke({"document_id": str(DOCUMENT_ID)}))


def test_agent_tool_set_is_read_only_and_complete() -> None:
    """工具集合必须正好是那两个只读工具。

    这条测试守护 ADR 0003 的边界：v1 的 Agent 只读。日后有人加了写工具，这里会立刻失败，
    迫使他先去改 ADR，而不是悄悄扩大模型的权限——模型上下文里有 RSS 抓来的外部文本，
    有写能力的最坏情况是数据被污染，只读的最坏情况只是答错。
    """

    tools = build_agent_tools(
        search_service=FakeSearchService([]),  # type: ignore[arg-type]
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
    )

    assert [each.name for each in tools] == ["search_news", "read_document"]
