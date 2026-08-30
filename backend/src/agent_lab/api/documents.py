"""提供按需读取 PostgreSQL 新闻全文的只读接口 ``GET /documents/{document_id}``。

本模块位于 FastAPI 与 Repository 边界：只有用户明确打开阅读视图时才查询
``documents.content_text``，不参与 document search 的每篇结果组装，也不访问 Qdrant、
Ollama 或执行任何写操作。响应只暴露前端阅读所需的业务字段，不返回 ORM 内部状态。
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.db.session import get_db_session
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.schemas.document_search import DocumentDetailResponse


logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    """为一次全文读取请求创建绑定当前 Session 的 Repository。"""

    return DocumentRepository(session)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="读取一篇新闻的完整正文",
    # 显式写 description，下面那份 docstring 就留给维护者，不进 OpenAPI。
    description="按 document_id 读取一篇新闻的完整纯文本正文及其元数据。",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "文档不存在或关联来源缺失。",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "PostgreSQL 当前不可用。",
        },
    },
)
async def get_document(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> DocumentDetailResponse:
    """从 PostgreSQL 读取一篇新闻完整纯文本及必要元数据。

    Args:
        document_id: 路径中的 PostgreSQL ``documents.id`` UUID。
        repository: 当前请求独占的异步文档 Repository。

    Returns:
        包含当前正文 hash、revision 和 ``content_text`` 的安全详情 DTO。

    Raises:
        HTTPException: 文档或关联 source 不存在时返回脱敏 404；数据库失败返回 503；
            数据库记录违反公开契约时返回 502。

    Notes:
        只执行一次 eager-load source 的 PostgreSQL 查询；不会重新切分正文或查询
        Qdrant Chunk，也不会把 ORM 对象直接交给 FastAPI 序列化。
    """

    # 1、一次查询把 source 一起 eager-load 出来。下面要用 source.name，分两次查会多一趟
    #    往返，而且 Session 在响应组装时可能已经关了。
    try:
        record = await repository.get_with_source(document_id)
    except SQLAlchemyError as exc:
        # 只记异常类名，不记 str(exc)——里面可能有连接串或 SQL。
        logger.error("文档读取失败 error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="文档服务不可用。",
        ) from exc

    # 2、source_id 是非空外键，但历史数据或人工修复可能留下 relationship 缺失；
    #    对外统一按「文档不可用」处理，不暴露数据库结构细节。
    if record is None or record.source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在。",
        )

    # 3、逐字段手搭 DTO，不用 model_validate(record)：只有列在这里的字段才会出去，
    #    以后往表里加列不会自动泄漏。校验失败按 502——请求没问题，是库里的数据不合契约。
    try:
        return DocumentDetailResponse(
            document_id=record.id,
            content_hash=record.content_hash,
            revision=record.index_revision,
            title=record.title,
            url=record.url,
            source_name=record.source.name,
            published_at=record.published_at,
            authors=list(record.authors),
            labels=list(record.labels),
            content_text=record.content_text,
        )
    except ValidationError as exc:
        # 不把具体字段值或正文放入日志/响应，避免坏数据成为敏感信息回显路径。
        logger.error("文档响应契约校验失败 error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="文档数据无效。",
        ) from None


__all__ = ["get_document_repository", "router"]
