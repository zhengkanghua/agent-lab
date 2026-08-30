"""把「读取一篇新闻全文」包装成模型可调用的 Tool。

本模块位于 Agent 层，是 ``repositories.document_repository.get_with_source`` 的薄封装：
只做「按 id 读一行 → 截断到上下文预算内 → 排成文本」三件事。它不写 PostgreSQL、不访问
Qdrant 或 Ollama、不改 processing_status，也不捕获数据库异常（异常要交给中间件重试和脱敏）。

会话生命周期是本模块唯一的复杂点：Agent 是进程级共享的，而 ``AsyncSession`` 是一次工作
单元，不能被长期持有或跨并发请求共用。所以这里注入的是 session **工厂**，每次工具调用
自己开一个短命 Session、用完立即归还连接池。
"""

from collections.abc import Callable
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.agent.limits import READ_DOCUMENT_MAX_CHARS
from agent_lab.models.document import DocumentRecord
from agent_lab.repositories.document_repository import DocumentRepository


type SessionFactory = Callable[[], AsyncSession]


class ReadDocumentArguments(BaseModel):
    """``read_document`` 的参数契约。

    只有一个参数，且它必须来自上一次 ``search_news`` 的输出——模型无法凭空构造出库里
    存在的 UUID。description 明确这一点，是为了减少模型「猜一个 id 试试」的行为。
    """

    document_id: UUID = Field(
        description=(
            "要读取的新闻的 document_id，必须是 search_news 结果里出现过的那个 UUID，"
            "原样照抄，不要改写或猜测。"
        ),
    )

    model_config = ConfigDict(extra="forbid")


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

    @tool("read_document", args_schema=ReadDocumentArguments)
    async def read_document(document_id: UUID) -> str:
        """读取一篇新闻的完整正文，用于检索片段不足以回答时补充上下文。

        Args:
            document_id: 来自 search_news 结果的新闻 UUID。

        Returns:
            带元数据和正文的文本；文档不存在时返回明确说明而不是抛异常——「查不到这一篇」
            是正常的业务结果，模型应当据此改换思路（比如重新检索），而不是被中间件重试
            三次再收到一句系统故障。

        Raises:
            SQLAlchemyError: 数据库不可用或查询失败。这类是真故障，向上抛给中间件。

        Notes:
            纯读取：一次 eager-load source 的查询，不写库、不重新切分正文、不查 Qdrant。
            正文超过上限时截断，不会把整篇塞进模型上下文。
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


__all__ = ["ReadDocumentArguments", "SessionFactory", "build_read_document_tool"]
