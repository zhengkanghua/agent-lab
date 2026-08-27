"""定义本地内部账号的 FastAPI Users 管理规则。

本模块只负责用户 ID 解析和创建/修改密码时的最低强度校验；公开注册、找回密码和邮箱
验证路由均不挂载。真实密码 Hash 由 FastAPI Users 的 pwdlib PasswordHelper 生成。
"""

from uuid import UUID

from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions

from news_vector_service.models.user import UserRecord
from news_vector_service.schemas.auth import AuthUserCreate


def validate_password_strength(password: str, email: str) -> None:
    """执行所有受信建号和改密入口共享的密码规则。

    Args:
        password: 尚未 Hash 的候选密码；调用方不得记录或持久化明文。
        email: 该密码所属的登录邮箱，用于拒绝完全相同的弱密码。

    Raises:
        InvalidPasswordException: 密码不在 12 到 128 字符之间，或与邮箱完全相同。
    """

    if not 12 <= len(password) <= 128:
        raise exceptions.InvalidPasswordException(
            reason="密码必须包含 12 到 128 个字符。"
        )
    if password.casefold() == email.casefold():
        raise exceptions.InvalidPasswordException(
            reason="密码不能与登录邮箱完全相同。"
        )


class UserManager(UUIDIDMixin, BaseUserManager[UserRecord, UUID]):
    """内部账号生命周期管理器；实例按 FastAPI 请求或 CLI 工作单元创建。"""

    async def validate_password(
        self,
        password: str,
        user: AuthUserCreate | UserRecord,
    ) -> None:
        """拒绝过短或与登录邮箱完全相同的密码。

        Args:
            password: CLI 创建账号或未来可信管理操作提交的明文密码。
            user: 与密码关联的账号创建模型或已持久化用户。

        Raises:
            InvalidPasswordException: 密码少于 12 字符，或与邮箱标识完全相同。

        Notes:
            仅执行内存校验，不记录密码，不执行数据库或网络 I/O；真正 Hash 在校验通过后
            由 ``BaseUserManager.create`` 执行。
        """

        validate_password_strength(password, str(user.email))
