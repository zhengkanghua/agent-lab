"""持久处理的项目契约；领取快照不依赖 ORM 或事务生命周期。"""

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from agent_lab.knowledge.processing.contracts import ProcessingValue
from agent_lab.knowledge.storage import ObjectReference


class ProcessingApplicationError(Exception):
    """用例只传播稳定错误码，由 HTTP 契约提供脱敏文案。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SourceIntake:
    """远端写入之前必须保存的对象身份和期望摘要。"""

    id: UUID
    document_id: UUID
    source_kind: str
    reference: ObjectReference
    mime_type: str
    metadata: dict[str, Any]

    @classmethod
    def prepare(cls, *, document_id: UUID, source_kind: str, data: bytes,
                mime_type: str, metadata: dict[str, Any] | None = None) -> "SourceIntake":
        identity = uuid4()
        return cls(
            identity, document_id, source_kind,
            ObjectReference(f"documents/{document_id}/sources/{identity}", len(data), sha256(data).hexdigest()),
            mime_type, dict(metadata or {}),
        )


@dataclass(frozen=True, slots=True)
class ProcessingReceipt:
    processing_id: UUID
    document_id: UUID
    state: str
    source_sha256: str
    candidate_revision: int = 1


class ProcessingClaim(ProcessingValue):
    """一次纯计算领取；候选修订与随机领取代次共同阻止迟到结果覆盖。"""

    id: UUID
    document_id: UUID
    candidate_revision: int
    claim_token: str
    source_object_key: str
    source_object_version: str | None
    source_sha256: str
    source_size: int
    source_mime_type: str
    title: str
    requires_review: bool = False
    draft_text: str | None = None
    draft_mime_type: str | None = None
