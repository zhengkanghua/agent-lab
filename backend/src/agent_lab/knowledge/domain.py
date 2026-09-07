"""KnowledgeBase 的业务身份与预期失败，不依赖 Web 或数据库框架。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


DEFAULT_KNOWLEDGE_BASE_KEY = "news"
DEFAULT_NEWS_KNOWLEDGE_BASE_ID = UUID("10000000-0000-4000-8000-000000000010")


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    """一个逻辑知识库的配置快照；key 是稳定身份，name 仅用于展示。"""

    id: UUID
    key: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseError(RuntimeError):
    """知识库组件向调用方报告的预期失败，不携带外部异常文本。"""


class KnowledgeBaseNotFoundError(KnowledgeBaseError):
    """目标知识库不存在。"""


class KnowledgeBaseInactiveError(KnowledgeBaseError):
    """目标知识库已停用，不能作为新绑定、同步或检索的目标。"""


class KnowledgeBaseKeyConflictError(KnowledgeBaseError):
    """稳定键已被另一个知识库使用。"""


class KnowledgeBaseStorageError(KnowledgeBaseError):
    """持久化适配器当前无法完成操作。"""


class SourceBindingError(RuntimeError):
    """Source 配置用例的稳定失败，不携带基础设施异常文本。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__()
        self.code = code
        self.detail = detail


class VectorIndexConfigurationError(RuntimeError):
    """当前索引规格与适配器实际使用的模型、切分或向量存储规格不一致。"""


def require_active_knowledge_base(knowledge_base: KnowledgeBase | None) -> KnowledgeBase:
    """统一启用语义；维护清理不调用此规则，仍允许显式清理停用库。"""

    if knowledge_base is None:
        raise KnowledgeBaseNotFoundError()
    if not knowledge_base.is_active:
        raise KnowledgeBaseInactiveError()
    return knowledge_base
