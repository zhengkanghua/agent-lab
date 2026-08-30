"""在应用启动时把环境 Secret 幂等同步为唯一保底超级管理员。

本模块只访问 PostgreSQL 的 users/access_tokens：不挂载 HTTP 路由，不访问 FreshRSS、
Ollama 或 Qdrant。环境密码只进入 pwdlib 校验/Hash，不写日志、异常或返回对象。
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_lab.auth.manager import validate_password_strength
from agent_lab.config.auth import AuthSettings, get_auth_settings
from agent_lab.db.session import async_session_factory
from agent_lab.models.user import AccessTokenRecord, UserRecord


@dataclass(frozen=True, slots=True)
class EnvironmentAdminSyncResult:
    """不含邮箱、密码或 Token 的启动同步安全摘要。"""

    configured: bool
    created: bool
    password_changed: bool
    released_previous_managers: int
    user_id: UUID | None


async def sync_configured_environment_admin() -> EnvironmentAdminSyncResult:
    """使用默认配置与 Session factory 同步环境保底管理员。

    Returns:
        只包含布尔状态、释放数量和用户 UUID 的脱敏结果。

    Raises:
        IntegrityError: 两次并发冲突重试后数据库仍无法满足唯一约束。
        SQLAlchemyError: PostgreSQL 不可用或认证表尚未迁移。

    Notes:
        最多执行两次 PostgreSQL 事务；不访问新闻、Embedding、Qdrant 或远程网络。
    """

    return await synchronize_environment_admin(
        get_auth_settings(),
        async_session_factory,
    )


async def synchronize_environment_admin(
    settings: AuthSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> EnvironmentAdminSyncResult:
    """同步、切换或解除唯一环境管理员标记，并处理并发启动竞争。

    Args:
        settings: 已校验且用 SecretStr 包装密码的认证配置。
        session_factory: 应用进程级异步 Session factory。

    Returns:
        不含环境邮箱、明文密码、Hash 或 Token 的同步结果。

    Raises:
        IntegrityError: 两次尝试都遇到并发唯一约束冲突。
        SQLAlchemyError: 其他 PostgreSQL 查询、写入或提交失败。
        InvalidPasswordException: 调用方绕过 AuthSettings 构造了不合规密码。
    """

    # 最多试两次。为什么要重试：多个进程同时启动时，两边可能都发现「这个邮箱还没有账号」
    # 然后一起插入，输的那个撞唯一约束。第二次跑的时候赢家已经提交了，那条路径会走到
    # 「找到已有账号」分支，不会再冲突。只给一次重试机会就够了——冲突的成因是「同时创建」，
    # 这件事不会发生第二轮。
    last_integrity_error: IntegrityError | None = None
    for _attempt in range(2):
        async with session_factory() as session:
            try:
                return await _synchronize_once(session, settings)
            except IntegrityError as error:
                last_integrity_error = error
                await session.rollback()

    assert last_integrity_error is not None
    raise last_integrity_error


async def _synchronize_once(
    session: AsyncSession,
    settings: AuthSettings,
) -> EnvironmentAdminSyncResult:
    """在一个事务中完成一次环境管理员同步尝试。

    「环境管理员」是配置文件里那对邮箱和密码在库里对应的那个账号，作用是保底：万一所有
    超管都被禁用或删了，改配置重启就能拿回入口。所以每次启动都要把库对齐到配置，而且
    全局只能有一个账号带这个标记。

    Args:
        session: 本次尝试独占的 Session，同时是事务边界。
        settings: 已校验的认证配置。

    Returns:
        脱敏的同步结果，只有布尔量、数量和 UUID。

    Raises:
        IntegrityError: 并发启动撞上唯一约束，交给上层重试。
        InvalidPasswordException: 配置里的密码不合策略。

    Notes:
        一个 PostgreSQL 写事务。明文密码只用于校验强度和算 Hash，不落库、不写日志。
    """

    # 1、先锁住当前所有带标记的账号。锁在这里拿，是因为后面要把多余的标记摘掉，
    #    中间不能有别的进程插进来改。
    managed_users = list(
        await session.scalars(
            select(UserRecord)
            .where(UserRecord.is_environment_admin.is_(True))
            .with_for_update()
        )
    )

    # 2、配置里没配 → 把现有标记全摘掉就收工。注意只摘标记、不删账号：那个账号可能是
    #    真人在用的超管，删掉等于误伤。
    if not settings.environment_admin_configured:
        for user in managed_users:
            user.is_environment_admin = False
        await session.commit()
        return EnvironmentAdminSyncResult(
            configured=False,
            created=False,
            password_changed=False,
            released_previous_managers=len(managed_users),
            user_id=None,
        )

    # 3、取出配置里的邮箱和密码，先过一遍密码策略。放在建号之前，是为了让「配置里的密码
    #    太弱」在这里就报错，而不是等到建完号再回滚。
    assert settings.admin_email is not None
    assert settings.admin_password is not None
    email = str(settings.admin_email)
    password = settings.admin_password.get_secret_value()
    validate_password_strength(password, email)

    # 4、按邮箱找目标账号，并锁住。比较时两边都 casefold：注册接口不区分邮箱大小写，
    #    这里也得一致，否则配置里改个大小写就会多建一个账号。
    target = await session.scalar(
        select(UserRecord)
        .where(func.lower(UserRecord.email) == email.casefold())
        .with_for_update()
    )
    helper = PasswordHelper()
    created = target is None
    password_changed = False

    # 5、没有就建、有就对齐密码。新建时 password_changed 记成 True 只是为了让返回结果
    #    如实反映「这个密码是这次写进去的」；下面第 7 步靠 created 把它排除在踢下线之外。
    if target is None:
        target = UserRecord(
            email=email,
            hashed_password=helper.hash(password),
            is_active=True,
            is_superuser=True,
            is_verified=True,
            is_environment_admin=False,
        )
        session.add(target)
        await session.flush()
        password_changed = True
    else:
        password_matches, upgraded_hash = helper.verify_and_update(
            password,
            target.hashed_password,
        )
        if not password_matches:
            target.hashed_password = helper.hash(password)
            password_changed = True
        elif upgraded_hash is not None:
            # 密码没变，但算法参数升级了（比如 bcrypt 轮数调高）。顺手换成新 Hash，
            # 不算密码变更，也就不用踢人下线。
            target.hashed_password = upgraded_hash

    # 6、把上一任的标记摘掉，保证全局只有一个环境管理员。flush 一下让 UPDATE 先走，
    #    避免和下面给 target 上标记的语句在同一批里撞上唯一约束。
    released = 0
    for previous in managed_users:
        if previous.id != target.id:
            previous.is_environment_admin = False
            released += 1
    if released:
        await session.flush()

    # 7、强制把目标账号拉回「可用的超管」状态。这是保底通道的意义所在：账号在库里被禁用、
    #    降权或标成未验证都不影响，改配置重启就能恢复。
    target.email = email
    target.is_active = True
    target.is_superuser = True
    target.is_verified = True
    target.is_environment_admin = True
    # 8、密码被这次同步改掉了 → 删掉他的 access token。旧密码签出去的会话不该在密码换了
    #    之后还能用。新建的账号没有历史会话，所以排除 created。
    if password_changed and not created:
        await session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == target.id)
        )

    # 9、提交后 refresh 一次，让 target.id 之类的字段确定可读，再组装脱敏结果。
    await session.commit()
    await session.refresh(target)
    return EnvironmentAdminSyncResult(
        configured=True,
        created=created,
        password_changed=password_changed,
        released_previous_managers=released,
        user_id=target.id,
    )
