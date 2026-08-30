"""把只读语义检索包装成模型可调用的 Tool。

本模块位于 Agent 层，是 ``services.vector_search_service.search_documents`` 的薄封装：
只做「参数收窄 → 调用 Service → 把结果排成模型好读的文本」三件事。它不生成 Embedding、
不访问 Qdrant 或 PostgreSQL（都由 Service 负责）、不写任何数据，也不捕获上游异常——
异常必须抛出去，否则 ``ToolRetryMiddleware`` 无从重试、``ToolErrorMiddleware`` 无从脱敏，
见 ``docs/adr/0005-middleware-order-semantics.md``。

为什么返回文本而不是 JSON：工具返回值会作为 ``ToolMessage`` 进入模型上下文，模型读的是
自然语言。JSON 同样能读，但会多花 token 在括号和引号上，且模型更容易照抄结构而不是理解
内容。这里排成带 document_id 的条目列表，既省 token 又保证引用所需的 id 一定在上下文里。
"""

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from agent_lab.agent.limits import (
    SEARCH_TOOL_MAX_DOCUMENTS,
    SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT,
)
from agent_lab.schemas.document_search import (
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.schemas.vector_search import MAX_QUERY_CHARACTERS
from agent_lab.services.vector_search_service import VectorSearchService


class SearchNewsArguments(BaseModel):
    """``search_news`` 的参数契约。

    这些字段的 ``description`` 会被 LangChain 转成 JSON Schema 塞进模型上下文，模型据此
    决定怎么填参数。所以它们同时是校验规则**和**提示词的一部分：写「必须是中文」这类约束
    没用（校验不了），写「按什么思路填」才有用。
    """

    query: str = Field(
        max_length=MAX_QUERY_CHARACTERS,
        description=(
            "要检索的内容，用陈述句或关键词描述你想找的新闻主题，例如「央行降息 房贷利率」。"
            "这是语义检索，不是关键词匹配，所以写完整的意思比堆关键词效果好；不要写成问句。"
        ),
    )
    document_limit: int = Field(
        default=SEARCH_TOOL_MAX_DOCUMENTS,
        ge=1,
        le=SEARCH_TOOL_MAX_DOCUMENTS,
        description=(
            f"最多返回几篇新闻，范围 1..{SEARCH_TOOL_MAX_DOCUMENTS}，默认取上限。"
            "只想确认某件事存不存在时可以填 1..2 以减少无关内容。"
        ),
    )

    model_config = ConfigDict(extra="forbid")


def build_search_news_tool(service: VectorSearchService) -> BaseTool:
    """用闭包把 Service 绑进 Tool，得到一个模型可直接调用的检索工具。

    为什么用闭包工厂而不是让工具自己去取 Service：工具函数的签名就是模型看到的参数表，
    多一个 ``service`` 参数模型就会试着去填它。闭包让依赖对模型完全不可见，同时保持
    可测——测试传入 fake Service 即可，不需要打补丁。

    Args:
        service: 进程级只读检索 Service，由 composition root 装配。

    Returns:
        名为 ``search_news`` 的 ``BaseTool``，已绑定 ``SearchNewsArguments`` 作为参数契约。

    Notes:
        本函数只组装对象，不执行检索。
    """

    @tool("search_news", args_schema=SearchNewsArguments)
    async def search_news(query: str, document_limit: int = SEARCH_TOOL_MAX_DOCUMENTS) -> str:
        """按语义检索已入库的新闻，返回若干篇的元数据和最相关片段。

        Args:
            query: 检索文本。
            document_limit: 最多返回几篇新闻。

        Returns:
            排好版的检索结果文本；没有命中时返回明确的空结果说明，不返回空字符串——
            空字符串会让模型以为工具坏了，进而重试或编造。

        Raises:
            OllamaEmbeddingError: query Embedding 失败（子类区分认证、超时、连接、模型缺失）。
            QueryVectorValidationError: Embedding 返回的向量不符合当前索引规格。
            QdrantVectorSearchError: Qdrant 查询失败。

        Notes:
            纯读取：一次 query Embedding 加一次 Qdrant 查询，不写 PostgreSQL 或 Qdrant。
            异常一律向上抛，交给中间件重试与脱敏。
        """

        request = DocumentSearchRequest(
            query=query,
            document_limit=document_limit,
            matches_per_document=SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT,
        )
        results = await service.search_documents(request)
        return _format_results(results)

    return search_news


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


__all__ = ["SearchNewsArguments", "build_search_news_tool"]
