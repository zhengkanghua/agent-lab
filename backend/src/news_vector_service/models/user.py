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
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from news_vector_service.db.base import Base, TimestampMixin


class UserRecord(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    """users 表：一个可登录且可独立禁用的内部平台账号。

    一行代表一个人工账号；``id`` 是 UUID 主键，规范化后的 email 是登录业务键。
    ``uq_users_email_lower`` 对 ``lower(email)`` 建唯一索引，使登录查询与数据库唯一性都
    忽略大小写。``hashed_password`` 只保存 pwdlib/Argon2 Hash。``is_active`` 控制账号
    是否可登录，``is_superuser`` 只用于授权高风险 Pipeline。继承的时间字段记录账号
    创建和最近一次 ORM 更新；本表与新闻表没有外键或 relationship。超级用户同时拥有
    手动 Pipeline 和账号管理权限，环境托管标记用于区分不可由网页降级的保底账号。
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "NOT is_environment_admin OR (is_active AND is_superuser AND is_verified)",
            name="ck_users_environment_admin_privileges",
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
            "是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管；全库最多一行，网页不可降级。"
        ),
    )


class AccessTokenRecord(SQLAlchemyBaseAccessTokenTableUUID, Base):
    """access_tokens 表：一个浏览器登录会话对应的一枚可撤销随机 Token。

    ``token`` 是 FastAPI Users 生成的 43 字符随机主键，也是 Cookie 携带的业务唯一键；
    ``user_id`` 外键指向 users.id，并在删除用户时级联清理。用户索引用于批量撤销账号
    会话，创建时间索引用于有效期查询和清理过期记录。本实体没有 ORM relationship，
    因为认证只按 Token 或用户 ID 定位，不需要隐式加载用户对象。
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
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="该登录 Token 所属的 users.id；删除用户时级联撤销。",
    )
