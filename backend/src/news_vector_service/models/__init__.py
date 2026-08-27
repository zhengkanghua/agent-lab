"""导入全部 ORM 模型，确保 Alembic 能发现对应表。"""

from news_vector_service.models.document import DocumentRecord
from news_vector_service.models.source import SourceRecord
from news_vector_service.models.user import AccessTokenRecord, UserRecord

__all__ = ["AccessTokenRecord", "DocumentRecord", "SourceRecord", "UserRecord"]
