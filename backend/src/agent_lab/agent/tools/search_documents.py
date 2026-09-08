"""检索 Tool 使用运行范围快照；只返回实际命中的片段与应用建立的引用标识。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import Field

from agent_lab.agent.context import AgentContext
from agent_lab.agent.evidence import DocumentEvidence, ToolEvidence
from agent_lab.agent.limits import SEARCH_TOOL_MAX_DOCUMENTS, SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT, SEARCH_TOOL_MAX_WITHIN_DAYS
from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.schemas.document_search import DocumentSearchRequest
from agent_lab.schemas.vector_search import MAX_QUERY_CHARACTERS, VectorSearchFilters
from agent_lab.services.vector_search_service import VectorSearchService


def scope_failure(runtime: ToolRuntime, content: str) -> ToolMessage:
    """范围问题无需重试上游，直接把可纠正的说明交给模型。"""
    return ToolMessage(content=content, tool_call_id=runtime.tool_call_id, status="error")


def build_search_documents_tool(service: VectorSearchService) -> BaseTool:
    """复用搜索用例与当前运行上下文，工具参数无法修改会话限制。"""

    @tool(parse_docstring=True)
    async def search_documents(
        query: Annotated[str, Field(max_length=MAX_QUERY_CHARACTERS)],
        runtime: ToolRuntime[AgentContext],
        document_limit: Annotated[int, Field(ge=1, le=SEARCH_TOOL_MAX_DOCUMENTS)] = SEARCH_TOOL_MAX_DOCUMENTS,
        within_days: Annotated[int | None, Field(ge=1, le=SEARCH_TOOL_MAX_WITHIN_DAYS)] = None,
        knowledge_base_ids: list[UUID] | None = None,
    ) -> ToolMessage:
        """按语义查找当前允许范围内的文档，返回标题、归属与实际命中片段。

        片段不足时可用 read_document 读取当前正文。每段前的 [[E...]] 是应用建立的引用，
        在所支持的事实结论后原样引用。查不到可在允许范围内改写查询，不能自动扩大范围。
        用户限定更窄知识库时从本次目录选择 ID；名称有歧义先询问，不猜测身份。

        Args:
            query: 用完整意思描述想查的内容，如「央行降息对房贷利率的影响」。
            runtime: 应用注入的本次运行上下文，模型不填写。
            document_limit: 最多返回几篇文档，具体上限见 schema。
            within_days: 只看最近若干天发布的文档，不填不限时间；填写后无发布时间的文件资料不会命中。只有用户要求时才填写，不擅自放宽用户的时间限制。
            knowledge_base_ids: 进一步缩小到目录中的非空 ID 列表；省略沿用本次运行允许范围。不能选择范围外的知识库。
        """
        context = runtime.context
        if context is None or context.scope is None:
            return scope_failure(runtime, "本次知识库范围未确认，请重新选择后提问。")
        scope = context.scope
        if knowledge_base_ids is not None:
            selected = set(knowledge_base_ids)
            if not selected or not selected <= set(scope.knowledge_base_ids):
                return scope_failure(runtime, "请求的知识库不在本次允许范围内，或选择为空。请在现有范围内查询，需要扩大时请用户修改页面范围。")
            scope = ResolvedKnowledgeBaseScope(
                mode="selected", knowledge_bases=tuple(item for item in scope.knowledge_bases if item.id in selected),
            )
        filters = VectorSearchFilters(
            published_from=datetime.now(UTC) - timedelta(days=within_days) if within_days is not None else None,
        )
        results = await service.search_documents(DocumentSearchRequest(
            query=query, document_limit=document_limit,
            matches_per_document=SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT, filters=filters,
        ), resolved_scope=scope)
        directory = {item.id: item for item in scope.knowledge_bases}
        evidence: list[DocumentEvidence] = []
        blocks = ["本次检索范围：" + "、".join(item.name for item in scope.knowledge_bases)]
        for result in results:
            # 外部检索结果必须仍属于已确认集合，不能把错配的 Point 包装成合法证据。
            if result.knowledge_base_id not in directory:
                raise ValueError("检索结果超出本次知识库范围")
            for match in (result.best_match, *result.additional_matches):
                item = DocumentEvidence(
                    document_id=result.document_id, knowledge_base_id=result.knowledge_base_id,
                    knowledge_base_name=directory[result.knowledge_base_id].name, title=result.title,
                    content_hash=result.content_hash, excerpt=match.page_content.strip(), kind="match",
                    source_name=result.source_name, upload_filename=result.upload_filename,
                    url=str(result.url) if result.url is not None else None, published_at=result.published_at,
                )
                evidence.append(item)
                published = result.published_at.strftime("%Y-%m-%d") if result.published_at else "发布时间未知"
                blocks.append(
                    f"[[{item.citation_id}]] {item.title}\n"
                    f"document_id: {item.document_id}\n知识库: {item.knowledge_base_name}\n"
                    f"来源: {item.source_name or item.upload_filename or '无外部来源'} | 发布: {published}\n"
                    f"实际命中片段:\n{item.excerpt}"
                )
        if not results:
            blocks.append("没有检索到相关文档。允许范围内可能没有这个主题，也可以换个说法再查；不要扩大范围补答案。")
        artifact = ToolEvidence(run_id=context.run_id, scope=scope, evidence=tuple(evidence))
        return ToolMessage(content="\n\n".join(blocks), artifact=artifact.model_dump(mode="json"), tool_call_id=runtime.tool_call_id, name="search_documents")

    return search_documents
