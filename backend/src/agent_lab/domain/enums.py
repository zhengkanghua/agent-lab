"""内部文档处理使用的有限状态枚举。

这些值会参与程序分支判断，因此由代码和数据库约束共同维护，而不是放入
可由管理员任意修改的通用字典表。
"""

from enum import StrEnum


class DocumentType(StrEnum):
    """统一文档的业务类型。"""

    ARTICLE = "article"
    PRESS_RELEASE = "press_release"
    ECONOMIC_RELEASE = "economic_release"
    FILING = "filing"
    RESEARCH_REPORT = "research_report"
    POLICY_DOCUMENT = "policy_document"



"""
PENDING：待索引（新文档 / 新版本 / 被重新排队的）
PROCESSING：已被某个 Worker 原子领走，正在切块/embedding/写 Qdrant
INDEXED：当前版本已完整写入 Qdrant（快照字段记录了写入详情）
FAILED：本次尝试失败，last_processing_error 存着脱敏后的原因
"""
class ProcessingStatus(StrEnum):
    """文档从发现到写入向量数据库的处理状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
