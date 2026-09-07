"""导入全部 ORM 模型，确保 Alembic 能发现对应表。"""

from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.document import DocumentRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.source import SourceRecord
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord

__all__ = [
    "AccessTokenRecord",
    "AgentThreadRecord",
    "DocumentRecord",
    "DocumentDeletionRecord",
    "WriteOperationRecord",
    "JobRunRecord",
    "KnowledgeBaseRecord",
    "ScheduledJobRecord",
    "SourceRecord",
    "UserRecord",
]
