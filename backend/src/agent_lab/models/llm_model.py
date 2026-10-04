"""可用模型表；一行是「某个上游渠道下面的一个模型」，用户能从这些条目里挑一个来用。

它挂在 ``llm_providers`` 下面，两者之间是**业务层维护的逻辑外键**：``provider_id`` 这一列
保留，库上没有 ``FOREIGN KEY``，随之没有级联——查一条模型所属渠道的启用状态由业务代码
显式 join（见 ``repositories/llm_model_repository.py``）。

**可用性是查出来的，不是存出来的**：一条模型对用户可用，当且仅当「它自己启用」且「所属渠道
也启用」。所以停用一条渠道时**不去逐个改模型的启用状态**——两份状态必然漂移；渠道的启用位
一改，挂在上面的模型跟着变得可用或不可用。

**「目录里恰好有一个默认」这条不变量有两个库上对象在守**：

- 同一渠道内上游模型名唯一（``UniqueConstraint``），管理员不用靠记。
- ``uq_llm_models_single_default`` 这条**部分唯一索引**兜住并发：默认标记只能被「把另一条设为
  默认」这一条路径改写，两个请求同时设默认时落败的一方会撞上它。索引不属于「不建外键约束」
  那条限制（根 AGENTS.md「业务与数据约束」第 3、4 条）。

本表只有停用，没有删除入口（与 ``KnowledgeBase``、``LlmProviderRecord`` 的「停用保留数据」一致）。
"""

from uuid import UUID, uuid4

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, Uuid, false, text, true
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class LlmModelRecord(TimestampMixin, Base):
    """一行代表一个可用模型：上游模型名、展示名、上下文窗口、默认与启用状态。

    ``provider_id`` 是逻辑外键（库上无约束）；``display_name`` 可空，留空时界面回落到
    ``upstream_model_name``；``context_window`` 必填、不允许留空——它同时是两件事的分母
    （压缩什么时候触发按它的比例算，一轮里单次工具输出能放多长也取它的比例）。
    """

    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "upstream_model_name",
            name="uq_llm_models_provider_upstream_name",
        ),
        Index(
            "uq_llm_models_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        {"comment": "可用模型：模型目录里用户能选到的条目，挂在上游渠道下面。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4,
        comment="Python 服务生成的可用模型主键。",
    )
    provider_id: Mapped[UUID] = mapped_column(
        Uuid, nullable=False,
        comment="所属上游渠道 id。业务层维护的逻辑外键，库上无约束。",
    )
    upstream_model_name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="上游那一侧真实存在的模型名，直接交给模型客户端；同一渠道内不可重复。",
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="展示名称，只用于界面；留空时界面回落到上游模型名。",
    )
    context_window: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment=(
            "上游模型的上下文窗口 token 数，必填；压缩什么时候触发按它的比例算，"
            "一轮里单次工具输出能放多长也取它的比例。填小了会提前压缩并截断工具输出。"
        ),
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(),
        comment=(
            "是否是没选模型时使用的默认条目；全目录最多一条为真（部分唯一索引兜底），"
            "只能被「把另一条设为默认」这条路径改写。"
        ),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(),
        comment="是否启用；停用的模型保留配置，只是不再出现在可选列表里。",
    )
