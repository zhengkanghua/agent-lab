"""上传与 FreshRSS 的统一管理 HTTP；所有候选、草稿和原件仅供超级用户读取。"""

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from agent_lab.api.error_contract import SanitizedValidationRoute
from agent_lab.auth.dependencies import current_superuser
from agent_lab.knowledge.composition import build_document_deletion_application, build_document_review_application
from agent_lab.knowledge.deletion import DocumentDeletionApplication
from agent_lab.knowledge.processing.lifecycle import ProcessingReceipt
from agent_lab.knowledge.processing.review import DocumentReviewApplication
from agent_lab.knowledge.processing.review_contracts import ReviewDetail, VersionDetail
from agent_lab.models.user import UserRecord
from agent_lab.schemas.document_review import (
    AdoptRequest, ManagedDocumentList, ManagementRevisionRequest, ProcessingList,
    ReviewDecisionList, ReviewDecisionRequest, ReviewTargetRequest, SaveDraftRequest,
    StartReviewRequest, VersionList,
)
from agent_lab.schemas.knowledge_base import KnowledgeBaseErrorResponse

router = APIRouter(prefix="/document-management", tags=["document-management"],
                   dependencies=[Depends(current_superuser)], route_class=SanitizedValidationRoute,
                   responses={code: {"model": KnowledgeBaseErrorResponse} for code in (404, 409, 422, 503)})


def get_document_review_application() -> DocumentReviewApplication:
    return build_document_review_application()


def get_document_deletion_application() -> DocumentDeletionApplication:
    return build_document_deletion_application()


Service = Annotated[DocumentReviewApplication, Depends(get_document_review_application)]
Actor = Annotated[UserRecord, Depends(current_superuser)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=ManagedDocumentList, summary="分页查看所有文档及处理状态")
async def list_documents(service: Service, knowledge_base_id: UUID | None = None,
                         source_kind: Literal["file", "freshrss"] | None = None,
                         state: str | None = None, offset: Offset = 0, limit: Limit = 25):
    items = await service.list(knowledge_base_id=knowledge_base_id, source_kind=source_kind,
                               state=state, offset=offset, limit=limit + 1)
    return ManagedDocumentList(items=items[:limit], has_more=len(items) > limit)


@router.get("/{document_id}", response_model=ReviewDetail, summary="查看正文、结构及 Chunk 预览")
async def document_detail(document_id: UUID, service: Service, processing_id: UUID | None = None):
    return await service.detail(document_id, processing_id)


@router.delete("/{document_id}", status_code=204, summary="删除文档原件、全部历史与索引")
async def delete_document(document_id: UUID, revision: Annotated[int, Query(ge=1)],
                          management_revision: Annotated[int, Query(ge=1)],
                          service: Annotated[DocumentDeletionApplication, Depends(get_document_deletion_application)]):
    await service.delete(document_id, revision=revision, management_revision=management_revision)
    return Response(status_code=204)


@router.get("/{document_id}/candidates", response_model=ProcessingList, summary="查看来源与候选处理记录")
async def candidates(document_id: UUID, service: Service, offset: Offset = 0, limit: Limit = 25):
    items = await service.candidates(document_id, offset=offset, limit=limit + 1)
    return ProcessingList(items=items[:limit], has_more=len(items) > limit)


@router.post("/{document_id}/draft", response_model=ProcessingReceipt, summary="开始人工复核并保留最新草稿")
async def start_review(document_id: UUID, body: StartReviewRequest, service: Service):
    """已有草稿时保留草稿；仅开始复核不会改变正式正文。"""
    return await service.start(document_id, **body.model_dump())


@router.post("/{document_id}/use-latest-source", response_model=ProcessingReceipt, summary="明确换用最新来源作为草稿")
async def use_latest_source(document_id: UUID, body: ManagementRevisionRequest, service: Service):
    """替换当前人工草稿，已采用版本保持可用。"""
    return await service.start(document_id, management_revision=body.management_revision, use_latest=True)


@router.put("/candidates/{processing_id}/draft", response_model=ProcessingReceipt, summary="保存最新人工草稿")
async def save_draft(processing_id: UUID, body: SaveDraftRequest, service: Service):
    """保存编辑内容后立即返回；旧预览失效，需重新生成。"""
    return await service.save(processing_id, **body.model_dump())


@router.post("/candidates/{processing_id}/preview", response_model=ProcessingReceipt, status_code=202, summary="后台重新生成结构和 Chunk 预览")
async def preview(processing_id: UUID, body: ReviewTargetRequest, service: Service):
    return await service.preview(processing_id, **body.model_dump())


@router.post("/candidates/{processing_id}/adopt", response_model=ProcessingReceipt, status_code=202, summary="确认采用本次预览")
async def adopt(processing_id: UUID, body: AdoptRequest, service: Service, actor: Actor):
    """新索引准备成功后切换；准备失败时保留旧已采用版本。"""
    return await service.adopt(processing_id, actor_id=actor.id, **body.model_dump())


@router.post("/candidates/{processing_id}/reject", response_model=ProcessingReceipt, summary="拒绝并停止整篇文档使用")
async def reject(processing_id: UUID, body: ReviewDecisionRequest, service: Service, actor: Actor):
    """停止后续检索、Agent 和普通全文读取，保留原件及审核记录供修正。"""
    return await service.reject(processing_id, actor_id=actor.id, **body.model_dump())


@router.post("/candidates/{processing_id}/retry", response_model=ProcessingReceipt, status_code=202, summary="核对接收结果或重试失败阶段")
async def retry(processing_id: UUID, body: ReviewTargetRequest, service: Service, actor: Actor):
    return await service.retry(processing_id, actor_id=actor.id, **body.model_dump())


@router.get("/candidates/{processing_id}/original", summary="下载已保存的原始资料", response_class=Response)
async def original(processing_id: UUID, service: Service):
    data, filename = await service.original(processing_id)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/{document_id}/versions", response_model=VersionList, summary="查看已采用版本列表")
async def versions(document_id: UUID, service: Service, offset: Offset = 0, limit: Limit = 25):
    items = await service.versions(document_id, offset=offset, limit=limit + 1)
    return VersionList(items=items[:limit], has_more=len(items) > limit)


@router.get("/{document_id}/versions/{version_id}", response_model=VersionDetail, summary="查看不可变的已采用历史")
async def version(document_id: UUID, version_id: UUID, service: Service):
    return await service.version(document_id, version_id)


@router.get("/{document_id}/reviews", response_model=ReviewDecisionList, summary="查看审核结论及当时正文")
async def decisions(document_id: UUID, service: Service, offset: Offset = 0, limit: Limit = 25):
    items = await service.decisions(document_id, offset=offset, limit=limit + 1)
    return ReviewDecisionList(items=items[:limit], has_more=len(items) > limit)
