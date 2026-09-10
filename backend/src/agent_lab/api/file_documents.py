"""超级用户的文本文件管理入口；文件先校验，再交给共用应用用例。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from agent_lab.api.error_contract import SanitizedValidationRoute
from agent_lab.auth.dependencies import current_superuser
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.composition import build_file_document_service
from agent_lab.knowledge.file_application import FileDocumentService
from agent_lab.knowledge.files import MAX_FILE_BYTES
from agent_lab.schemas.file_document import FileDocumentListResponse, FileDocumentResponse
from agent_lab.schemas.knowledge_base import KnowledgeBaseErrorResponse

router = APIRouter(
    prefix="/file-documents", tags=["file-documents"],
    dependencies=[Depends(current_superuser)], route_class=SanitizedValidationRoute,
    responses={code: {"model": KnowledgeBaseErrorResponse} for code in (404, 409, 413, 422, 503)},
)


def get_file_document_service() -> FileDocumentService:
    return build_file_document_service()


Service = Annotated[FileDocumentService, Depends(get_file_document_service)]


@router.get("", response_model=FileDocumentListResponse, summary="查看上传文档及处理状态")
async def list_files(
    service: Service,
    knowledge_base_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> FileDocumentListResponse:
    """分页列出上传资料，包含停用知识库中的已有文件。"""
    items = await service.list(knowledge_base_id=knowledge_base_id, offset=offset, limit=limit + 1)
    return FileDocumentListResponse(
        items=[FileDocumentResponse.model_validate(item) for item in items[:limit]],
        has_more=len(items) > limit,
    )


async def _parse_upload(file: UploadFile):
    try:
        return parse_text_file(file.filename or "", await file.read(MAX_FILE_BYTES + 1))
    finally:
        await file.close()


@router.post("", response_model=FileDocumentResponse, status_code=201, summary="上传文本或 Markdown 文件")
async def upload_file(
    service: Service,
    knowledge_base_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> FileDocumentResponse:
    """保存原件与待办后返回，后台解析并采用；同名文件创建独立资料。"""
    parsed = await _parse_upload(file)
    return FileDocumentResponse.model_validate(await service.upload(parsed, knowledge_base_id))


@router.put("/{document_id}/file", response_model=FileDocumentResponse, summary="替换指定文档的文件")
async def replace_file(
    document_id: UUID, service: Service,
    revision: Annotated[int, Form(ge=1)],
    management_revision: Annotated[int, Form(ge=1)],
    file: Annotated[UploadFile, File()],
) -> FileDocumentResponse:
    """保留目标 ID 和知识库归属，版本冲突时拒绝覆盖。"""
    parsed = await _parse_upload(file)
    return FileDocumentResponse.model_validate(await service.replace(document_id, parsed, revision, management_revision))


@router.delete("/{document_id}", status_code=204, summary="删除指定上传文档及索引")
async def delete_file(
    document_id: UUID, service: Service, revision: Annotated[int, Query(ge=1)],
    management_revision: Annotated[int, Query(ge=1)],
) -> Response:
    """只有索引与文档删除均已确认才返回成功，失败可从同一目标继续。"""
    await service.delete(document_id, revision, management_revision)
    return Response(status_code=204)
