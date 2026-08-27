"""PostgreSQL 数据访问对象。"""

from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.source_repository import SourceRepository

__all__ = ["DocumentRepository", "SourceRepository"]
