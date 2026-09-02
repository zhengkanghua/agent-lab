"""把只读语义检索包装成模型可调用的 Tool。

本模块位于 Agent 层，是 ``services.vector_search_service.search_documents`` 的薄封装：
只做「参数收窄 → 调用 Service → 把结果排成模型好读的文本」三件事。

**参数收窄是有意的**：Service 那层支持六个过滤字段和 score_threshold，这里只向模型暴露
``query``、``document_limit`` 和 ``within_days``。判据是「模型有没有可能填对」——它拿不到
source_id 列表、不知道语料库里有哪些标签、也读不懂原始 Cosine score 的量纲，让它填这些
等于让它猜，而猜错的表现是「明明有这条新闻却回答说没有」，比不过滤糟得多。``within_days``
能给，是因为用户自己会说「最近三天」，模型只需把这句话里的数字搬过来。它不生成 Embedding、
不访问 Qdrant 或 PostgreSQL（都由 Service 负责）、不写任何数据，也不捕获上游异常——
异常必须抛出去，否则 ``ToolRetryMiddleware`` 无从重试、``ToolErrorMiddleware`` 无从脱敏，
见 ``docs/adr/0005-middleware-order-semantics.md``。

为什么返回文本而不是 JSON：工具返回值会作为 ``ToolMessage`` 进入模型上下文，模型读的是
自然语言。JSON 同样能读，但会多花 token 在括号和引号上，且模型更容易照抄结构而不是理解
内容。这里排成带 document_id 的条目列表，既省 token 又保证引用所需的 id 一定在上下文里。

工具函数实现说明
----------------
search_news:
    参数说明在函数 docstring 的 Args 部分，校验规则通过 ``Annotated[type, Field(...)]`` 定义。

    Returns:
        格式化的新闻列表文本，见 ``_format_results``。没有命中时刻意返回一句说明而不是空字符串：
        空字符串会让模型以为工具坏了，进而重试或编造。

    Raises:
        OllamaEmbeddingError: query Embedding 失败（子类区分认证、超时、连接、模型缺失）。
        QueryVectorValidationError: 向量不符合当前索引规格。
        QdrantVectorSearchError: Qdrant 查询失败。
        一律向上抛，交给 ``ToolRetryMiddleware`` 重试、``ToolErrorMiddleware`` 脱敏；
        这几个类名不写进工具 docstring，否则会随工具描述进模型上下文，绕过 ``sanitize_tool_error``。

    I/O:
        纯读取：一次 query Embedding 加一次 Qdrant 查询，不写 PostgreSQL 或 Qdrant。
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from agent_lab.agent.limits import (
    SEARCH_TOOL_MAX_DOCUMENTS,
    SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT,
    SEARCH_TOOL_MAX_WITHIN_DAYS,
)
from agent_lab.schemas.document_search import (
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.schemas.vector_search import MAX_QUERY_CHARACTERS, VectorSearchFilters
from agent_lab.services.vector_search_service import VectorSearchService


def build_search_news_tool(service: VectorSearchService) -> BaseTool:
    """用闭包把 Service 绑进 Tool，得到一个模型可直接调用的检索工具。

    为什么用闭包工厂而不是让工具自己去取 Service：工具函数的签名就是模型看到的参数表，
    多一个 ``service`` 参数模型就会试着去填它。闭包让依赖对模型完全不可见，同时保持
    可测——测试传入 fake Service 即可，不需要打补丁。

    Args:
        service: 进程级只读检索 Service，由 composition root 装配。

    Returns:
        名为 ``search_news`` 的 ``BaseTool``。

    Notes:
        本函数只组装对象，不执行检索。
    """

    @tool(parse_docstring=True)
    async def search_news(
        query: Annotated[str, Field(max_length=MAX_QUERY_CHARACTERS)],
        document_limit: Annotated[
            int, Field(default=SEARCH_TOOL_MAX_DOCUMENTS, ge=1, le=SEARCH_TOOL_MAX_DOCUMENTS)
        ] = SEARCH_TOOL_MAX_DOCUMENTS,
        within_days: Annotated[
            int | None, Field(default=None, ge=1, le=SEARCH_TOOL_MAX_WITHIN_DAYS)
        ] = None,
    ) -> str:
        """按语义检索已入库的新闻，返回若干篇的标题、来源、发布时间和最相关片段。

        用户问「有没有」「最近怎么说」「什么情况」这类需要查资料的问题时，先用它。
        一篇新闻的完整正文不在结果里，片段不够时再用 read_document 按 document_id 取。

        检索不到时会返回一句明确的说明，不是空结果，此时可以换个说法再检索一次，或者
        直接告诉用户语料库里没有这个主题。用 within_days 缩小过时间范围而没有结果时，
        可以去掉这个参数再检索一次，语料库的时间覆盖可能和用户以为的不一样。

        Args:
            query: 要检索的内容，用陈述句或关键词描述你想找的新闻主题，例如「央行降息 房贷利率」。这是语义检索，不是关键词匹配，所以写完整的意思比堆关键词效果好；不要写成问句。
            document_limit: 最多返回几篇新闻，默认取上限。只想确认某件事存不存在时可以填 1..2 以减少无关内容。具体范围见参数 schema 的 minimum/maximum。
            within_days: 只看最近多少天内发布的新闻；不填表示不限发布时间。用户明确说了时间范围（「最近三天」「这一周」「今年」）时填对应天数，否则不要填——填了会把窗口外确实相关的新闻整条排除掉，而语义检索本来就倾向于把切题的排在前面。注意语料库里有些新闻没有发布时间，填了这个参数它们一律不会出现。具体范围见参数 schema 的 minimum/maximum。
        """

        request = DocumentSearchRequest(
            query=query,
            document_limit=document_limit,
            matches_per_document=SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT,
            filters=_time_filters(within_days),
        )
        results = await service.search_documents(request)
        return _format_results(results)

    return search_news


def _time_filters(within_days: int | None) -> VectorSearchFilters:
    """把「最近多少天」换算成 Qdrant 用的发布时间下界。

    只填 ``published_from``，其余五个过滤字段一律留空：来源、类型、标签这些的取值模型
    看不到（它拿不到 source_id 列表，也不知道有哪些标签），让它填等于让它猜，猜错的表现
    是「明明有这条新闻却说没有」——比不过滤糟得多。

    Args:
        within_days: 天数；``None`` 表示不限时间。范围由工具参数的 ``Annotated`` 约束校验，
            这里不重复校验。

    Returns:
        只带 ``published_from`` 的过滤条件，或全空的过滤条件。

    Notes:
        纯计算，不执行 I/O。基准时间取 UTC 当下，与 ``published_at`` 的存储时区一致。
        减法用整天：``within_days=1`` 表示「往回 24 小时」，不是「今天零点以后」。后者
        会让凌晨提问时的窗口缩到几个小时，而用户说「最近一天」并不是这个意思。
    """

    if within_days is None:
        return VectorSearchFilters()
    return VectorSearchFilters(published_from=datetime.now(UTC) - timedelta(days=within_days))


def _format_results(results: list[DocumentSearchResult]) -> str:
    """把检索结果排成模型易读、且带齐引用所需字段的文本。

    Args:
        results: Service 返回的按 bestScore 降序的文档级结果。

    Returns:
        每篇一段的纯文本。刻意包含 document_id（模型引用和调用 read_document 都要用）
        和发布时间（新闻的时效性影响结论），刻意不包含 score——score 是原始 Cosine
        相似度，不是置信度，给模型看只会诱导它把「相似度 0.8」说成「80% 相关」。

    Notes:
        纯字符串处理，不执行 I/O。
    """

    if not results:
        return "没有检索到相关新闻。语料库里可能确实没有这个主题，也可能换个说法能找到。"

    blocks: list[str] = [f"检索到 {len(results)} 篇相关新闻：\n"]
    for order, result in enumerate(results, start=1):
        published = (
            result.published_at.strftime("%Y-%m-%d") if result.published_at else "发布时间未知"
        )
        excerpts = [result.best_match, *result.additional_matches]
        excerpt_text = "\n".join(f"    - {match.page_content.strip()}" for match in excerpts)
        blocks.append(
            f"{order}. {result.title}\n"
            f"   document_id: {result.document_id}\n"
            f"   来源: {result.source_name} | 发布: {published}\n"
            f"   相关片段:\n{excerpt_text}"
        )
    return "\n\n".join(blocks)


__all__ = ["build_search_news_tool"]
