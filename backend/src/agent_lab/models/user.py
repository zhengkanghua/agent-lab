"""声明本地登录用户与可撤销数据库访问 Token 的 PostgreSQL 实体。

本模块位于 SQLAlchemy 持久层，只保存 FastAPI Users 认证所需的账号状态、密码 Hash
和登录 Token；不接收明文密码、不实现登录路由，也不保存浏览器 Cookie。新闻内容仍由
``documents`` 表管理，用户与新闻当前没有租户或所有权关系。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.access_token import (
    SQLAlchemyBaseAccessTokenTableUUID,
)
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class UserRecord(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    """users 表：一个可登录且可独立禁用的内部平台账号。

    一行代表一个人工账号；``id`` 是 UUID 主键，规范化后的 email 是登录业务键。
    ``uq_users_email_lower`` 对 ``lower(email)`` 建唯一索引，使登录查询与数据库唯一性都
    忽略大小写。``hashed_password`` 只保存 pwdlib/Argon2 Hash。``is_active`` 控制账号
    是否可登录，``is_superuser`` 只用于授权高风险 Pipeline。继承的时间字段记录账号
    创建和最近一次 ORM 更新；本表与新闻表没有逻辑外键或 relationship，指向本表的
    ``access_tokens``、``agent_threads``、``document_review_records``、``user_preferences``
    四列都是逻辑外键。超级用户同时拥有手动 Pipeline 和账号管理权限，环境托管标记用于区分
    不可由网页改动的那个超级用户。

    **账号不再硬删除，只注销。** ``deleted_at`` 有值表示这个账号已经注销：账号行、会话归属、
    个人偏好和换版决策留痕全部保留，只有 ``access_tokens`` 在该事务里被清掉（决策见
    ``docs/adr/0034-account-deletion-is-soft-delete.md``）。注销必须
    **同时**写 ``is_active=false`` 与 ``deleted_at``：登录与认证两条路都只看前者，只写时间戳
    挡不住登录。两个字段的组合由 ``ck_users_deleted_at_implies_inactive`` 在库层兜底。

    三个状态由这两个字段组合出来：活跃 = ``is_active``；停用 = ``is_active`` 为假且未注销；
    注销 = ``deleted_at`` 有值（此时 ``is_active`` 必为假）。
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "NOT is_environment_admin OR "
            "(is_active AND is_superuser AND is_verified AND deleted_at IS NULL)",
            name="ck_users_environment_admin_privileges",
        ),
        # 配对约束：拦住「已注销却仍可登录」这一种组合。它的值在于不拦任何正常写入，而防的是
        # 那条静默的越权登录——``is_active`` 有多个写者（启动同步、两类建号、改状态），任何
        # 一个漏掉与 ``deleted_at`` 配对，表现都是「列表显示已注销、这个人照常登录」且没有报错。
        CheckConstraint(
            "NOT (deleted_at IS NOT NULL AND is_active)",
            name="ck_users_deleted_at_implies_inactive",
        ),
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        Index(
            "uq_users_single_environment_admin",
            "is_environment_admin",
            unique=True,
            postgresql_where=text("is_environment_admin"),
        ),
        {"comment": "可登录 News RAG Platform 的内部人工账号。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        comment="应用生成的登录用户 UUID 主键。",
    )
    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        comment="大小写不敏感的登录邮箱；仅作账号标识，当前不用于发送邮件。",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="由 pwdlib 生成的密码 Hash；永不保存或返回明文密码。",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="账号是否允许登录和继续使用已有登录 Token。",
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否允许执行手动 Pipeline 等高权限操作。",
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="账号是否由管理员确认；CLI 创建的封闭账号直接标记为已确认。",
    )
    is_environment_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment=(
            "是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管；全库最多一行，网页不可停用或注销。"
        ),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="账号注销时间；为空表示账号未被注销。注销时与 is_active=false 成对写入。",
    )


class AccessTokenRecord(SQLAlchemyBaseAccessTokenTableUUID, Base):
    """access_tokens 表：一个浏览器登录会话对应的一枚可撤销随机 Token。

    ``token`` 是 FastAPI Users 生成的 43 字符随机主键，也是 Cookie 携带的业务唯一键；
    ``user_id`` 是逻辑外键，指向 users.id，库上没有约束，注销账号时由业务层撤销这些 Token。
    用户索引用于批量撤销账号会话，创建时间索引用于有效期查询和清理过期记录。本实体没有
    ORM relationship，因为认证只按 Token 或用户 ID 定位，不需要隐式加载用户对象。
    """

    __tablename__ = "access_tokens"
    __table_args__ = (
        Index("ix_access_tokens_created_at", "created_at"),
        Index("ix_access_tokens_user_id", "user_id"),
        {"comment": "浏览器账号密码登录产生的可撤销数据库访问 Token。"},
    )

    token: Mapped[str] = mapped_column(
        String(43),
        primary_key=True,
        comment="FastAPI Users 生成并写入 HttpOnly Cookie 的高熵随机登录 Token。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="Token 创建时间；DatabaseStrategy 据此判断会话是否过期。",
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        comment="该登录 Token 所属的 users.id；业务层维护的逻辑外键，库上无约束。注销账号时由业务层撤销。",
    )
