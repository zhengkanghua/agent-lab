"""有界读取当前 Document；无 Source 的上传资料同样可读，每次使用短事务。"""

from collections.abc import Callable
from uuid import UUID

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.agent.context import AgentContext
from agent_lab.agent.evidence import DocumentEvidence, ToolEvidence
from agent_lab.agent.limits import READ_DOCUMENT_MAX_CHARS
from agent_lab.agent.tools.search_documents import scope_failure
from agent_lab.repositories.document_repository import DocumentRepository

type SessionFactory = Callable[[], AsyncSession]


def build_read_document_tool(session_factory: SessionFactory) -> BaseTool:
    """只读 PostgreSQL，不持有长事务、不读取范围外正文到模型上下文。"""

    @tool(parse_docstring=True)
    async def read_document(document_id: UUID, runtime: ToolRuntime[AgentContext]) -> ToolMessage:
        """按 document_id 读取本次允许知识库中的当前正文，片段不足时使用。

        来源和发布时间可为空。正文过长会明确截断，未读取的部分不能当成不存在。
        当前正文可能已替换，必须使用本次读取给出的新 [[E...]] 标识引用当前内容，
        不沿用旧命中片段的引用。文档删除、知识库停用和范围外都会明确说明。

        Args:
            document_id: 检索结果中实际出现的文档 UUID，原样照抄，不猜测。
            runtime: 应用注入的本次运行上下文，模型不填写。
        """
        context = runtime.context
        if context is None or context.scope is None:
            return scope_failure(runtime, "本次知识库范围未确认，请重新选择后提问。")
        async with session_factory() as session:
            record = await DocumentRepository(session).get_with_source(document_id)
            if record is None:
                return scope_failure(runtime, f"没有找到 document_id 为 {document_id} 的文档，原文档可能已删除。请重新检索。")
            if record.knowledge_base_id not in context.scope.knowledge_base_ids:
                return scope_failure(runtime, "该文档不属于本次允许的知识库，不能读取。需要扩大时请用户修改页面范围。")
            if not record.knowledge_base.is_active:
                return scope_failure(runtime, "该文档所属知识库已停用，当前不能读取；这不表示文档已删除。")
            content = record.content_text.strip()
            item = DocumentEvidence(
                document_id=record.id, knowledge_base_id=record.knowledge_base_id,
                knowledge_base_name=record.knowledge_base.name, title=record.title,
                content_hash=record.content_hash, excerpt=content[:READ_DOCUMENT_MAX_CHARS],
                kind="document", truncated=len(content) > READ_DOCUMENT_MAX_CHARS,
                source_name=record.current_version.metadata_snapshot.get("source_name"),
                upload_filename=record.upload_filename, url=record.url, published_at=record.published_at,
            )
            published = record.published_at.strftime("%Y-%m-%d %H:%M") if record.published_at else "发布时间未知"
            body = (
                f"[[{item.citation_id}]] 标题: {item.title}\n"
                f"document_id: {item.document_id}\n知识库: {item.knowledge_base_name}\n"
                f"来源: {item.source_name or item.upload_filename or '无外部来源'} | 发布: {published}\n"
                f"作者: {'、'.join(record.authors) if record.authors else '未署名'}\n"
                f"原文地址: {record.url or '无外部地址'}\n\n当前正文:\n{item.excerpt}"
            )
            if item.truncated:
                body += f"\n\n[正文超过 {READ_DOCUMENT_MAX_CHARS} 字，以上是前半部分，后续内容未读取]"
            artifact = ToolEvidence(run_id=context.run_id, scope=context.scope, evidence=(item,))
            return ToolMessage(content=body, artifact=artifact.model_dump(mode="json"), tool_call_id=runtime.tool_call_id, name="read_document")

    return read_document
