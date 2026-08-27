"""PostgreSQL 数据访问对象。"""

from news_vector_service.repositories.document_repository import DocumentRepository
from news_vector_service.repositories.source_repository import SourceRepository

__all__ = ["DocumentRepository", "SourceRepository"]
