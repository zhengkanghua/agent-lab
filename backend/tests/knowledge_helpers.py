"""离线搜索组件的范围替身；真实启停用例使用独立 MemoryStore 验证。"""

from datetime import UTC, datetime
from uuid import UUID

from agent_lab.knowledge.domain import KnowledgeBase


class ActiveKnowledgeBaseScope:
    """测试已选定范围时模拟启用库，记录准入次数供调用顺序断言。"""

    def __init__(self):
        self.calls: list[UUID] = []

    async def require_active(self, knowledge_base_id: UUID) -> KnowledgeBase:
        self.calls.append(knowledge_base_id)
        now = datetime(2026, 9, 7, tzinfo=UTC)
        return KnowledgeBase(
            id=knowledge_base_id, key="test", name="测试知识库", description=None,
            is_active=True, created_at=now, updated_at=now,
        )
