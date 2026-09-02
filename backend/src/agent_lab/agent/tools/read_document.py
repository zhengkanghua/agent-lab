"""把「读取一篇新闻全文」包装成模型可调用的 Tool。

本模块位于 Agent 层，是 ``repositories.document_repository.get_with_source`` 的薄封装：
只做「按 id 读一行 → 截断到上下文预算内 → 排成文本」三件事。它不写 PostgreSQL、不访问
Qdrant 或 Ollama、不改 processing_status，也不捕获数据库异常（异常要交给中间件重试和脱敏）。

会话生命周期是本模块唯一的复杂点：Agent 是进程级共享的，而 ``AsyncSession`` 是一次工作
单元，不能被长期持有或跨并发请求共用。所以这里注入的是 session **工厂**，每次工具调用
自己开一个短命 Session、用完立即归还连接池。

工具函数实现说明
----------------
read_document:
    参数只有 document_id，说明在函数 docstring 的 Args 部分。

    Returns:
        格式化的新闻全文（标题、来源、发布时间、正文），见 ``_format_document``。
        文档不存在时返回说明文案而不是抛异常：「查不到这一篇」是正常业务结果，
        模型应当据此改换思路（比如重新检索），而不是被中间件重试三次再收到系统故障。

    Raises:
        SQLAlchemyError: 数据库不可用或查询失败。这类是真故障，向上抛给中间件；
        异常类名不写进工具 docstring，否则会随工具描述进模型上下文，绕过
        ``sanitize_tool_error`` 的脱敏。

    I/O:
        纯读取：一次 eager-load source 的查询，不写库、不重新切分正文、不查 Qdrant。
"""

from collections.abc import Callable
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.agent.limits import READ_DOCUMENT_MAX_CHARS
from agent_lab.models.document import DocumentRecord
from agent_lab.repositories.document_repository import DocumentRepository


type SessionFactory = Callable[[], AsyncSession]


def build_read_document_tool(session_factory: SessionFactory) -> BaseTool:
    """用闭包把 session 工厂绑进 Tool，得到一个模型可直接调用的全文读取工具。

    Args:
        session_factory: 每次调用产出一个独立 ``AsyncSession`` 的工厂，通常是
            ``db.session.async_session_factory``。传工厂而不是 Session，是因为工具的
            生命周期是进程级、Session 的生命周期是一次调用。

    Returns:
        名为 ``read_document`` 的 ``BaseTool``。

    Notes:
        本函数只组装对象，不建立数据库连接。
    """

    @tool(parse_docstring=True)
    async def read_document(document_id: UUID) -> str:
        """按 document_id 读取一篇新闻的完整正文。

        只在 search_news 返回的片段不足以回答、确实需要看上下文时才用；不要对检索结果里
        的每一篇都调用它。

        正文过长时会被截断并标注，看到截断标注就说明你只读到了前半部分，不要据此断言
        「文中没有提到」。document_id 查不到时会返回一句明确的说明，此时应当确认 id 是否
        来自 search_news 的结果，或者换个检索词重新检索。

        Args:
            document_id: 要读取的新闻的 document_id，必须是 search_news 结果里出现过的那个 UUID，原样照抄，不要改写或猜测。
        """

        async with session_factory() as session:
            repository = DocumentRepository(session)
            record = await repository.get_with_source(document_id)

            if record is None or record.source is None:
                return (
                    f"没有找到 document_id 为 {document_id} 的新闻。"
                    "请确认这个 id 来自 search_news 的结果，或者换个检索词重新检索。"
                )
            return _format_document(record)

    return read_document


def _format_document(record: DocumentRecord) -> str:
    """把一条文档记录排成模型易读的文本，并把正文截断到上下文预算内。

    Args:
        record: 已 eager-load ``source`` 的文档记录。

    Returns:
        含标题、来源、发布时间和正文的纯文本。正文超出
        ``READ_DOCUMENT_MAX_CHARS`` 时截断并显式标注，让模型知道自己看到的不完整——
        不标注的话它可能基于半篇文章下「文中没有提到」这类结论。

    Notes:
        纯字符串处理，不执行 I/O。不返回 content_hash 或 revision：模型用不上它们，
        「正文是否已过期」的判定在前端做，见 docs/adr/0002。
    """

    published = (
        record.published_at.strftime("%Y-%m-%d %H:%M") if record.published_at else "发布时间未知"
    )
    content = record.content_text.strip()
    if len(content) > READ_DOCUMENT_MAX_CHARS:
        content = (
            content[:READ_DOCUMENT_MAX_CHARS]
            + f"\n\n[正文超过 {READ_DOCUMENT_MAX_CHARS} 字，以上是前半部分，后续内容未读取]"
        )

    authors = "、".join(record.authors) if record.authors else "未署名"
    return (
        f"标题: {record.title}\n"
        f"document_id: {record.id}\n"
        f"来源: {record.source.name} | 发布: {published} | 作者: {authors}\n"
        f"原文地址: {record.url}\n\n"
        f"正文:\n{content}"
    )


__all__ = ["SessionFactory", "build_read_document_tool"]
