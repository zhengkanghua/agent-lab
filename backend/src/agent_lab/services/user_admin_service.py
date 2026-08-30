"""实现超级用户对内部账号、权限、密码和数据库会话的管理用例。

本 Service 只读写 PostgreSQL users/access_tokens，不负责 HTTP、Cookie 设置或公开注册。
环境管理员不可通过此层降级或改密；任何操作都不能移除最后一个启用的超级用户。
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi_users import exceptions
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.auth.manager import validate_password_strength
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminPasswordRequest,
    UserAdminUpdateRequest,
)


@dataclass(frozen=True, slots=True)
class UserAdminDomainError(Exception):
    """携带稳定安全代码的账号管理预期失败。"""

    code: str
    detail: str


# 对外密码策略文案。不转发 InvalidPasswordException.reason：该异常来自 fastapi-users，
# 其文本不受本项目控制，读它就等于把上游文本送进响应体。前端也只按 code 取文案。
INVALID_PASSWORD_DETAIL = "密码必须包含 12 到 128 个字符，且不能与登录邮箱完全相同。"


class UserAdminService:
    """以一个请求级 AsyncSession 管理用户和登录 Token 的事务边界。

    生命周期是「一个 HTTP 请求一个实例」，不能跨请求复用：它持有的 Session 就是本次请求
    的事务边界，每个公开方法自己 commit 或 rollback，调用方不需要再管事务。

    两条贯穿全类的业务约束：环境管理员（``is_environment_admin``）不能被本层降级或改密，
    只能通过服务端密钥改；任何操作都不能让系统失去最后一个「启用且是超管」的账号，否则
    没人能再进管理页。
    """

    def __init__(self, session: AsyncSession) -> None:
        """绑定本次请求的 Session 和一个密码哈希器。

        Args:
            session: 请求级 ``AsyncSession``，同时是本 Service 的事务边界。

        Notes:
            不执行 I/O。``PasswordHelper`` 每个实例新建一个，它无状态，开销只是对象分配。
        """

        self._session = session
        self._password_helper = PasswordHelper()

    async def list_users(self) -> list[UserRecord]:
        """按环境管理员优先、邮箱升序返回全部内部账号。

        Returns:
            当前 users 表中的 ORM 用户列表，不包含密码 Hash 的额外加载。

        Notes:
            执行一次 PostgreSQL 只读查询，不访问 access_tokens 或外部服务。
        """

        return list(
            await self._session.scalars(
                select(UserRecord).order_by(
                    UserRecord.is_environment_admin.desc(),
                    UserRecord.email.asc(),
                )
            )
        )

    async def create_user(self, request: UserAdminCreateRequest) -> UserRecord:
        """创建一个已确认、默认启用且不受环境托管的内部账号。

        ``is_verified`` 直接给 ``True``、``is_environment_admin`` 固定 ``False``：这是超管
        代建的内部账号，没有邮箱验证流程可走；而环境托管身份只能由启动时的引导逻辑赋予，
        不能从接口造出来，否则等于给了一个「本层改不动」的后门账号。

        Args:
            request: 邮箱、初始密码和是否超管。

        Returns:
            已落库并刷新的 ``UserRecord``。

        Raises:
            UserAdminDomainError: ``invalid_password`` 密码不合策略；
                ``user_already_exists`` 邮箱已被占用。

        Notes:
            一次 PostgreSQL 写入。密码只以 Hash 落库，明文不写日志、不进异常消息。
        """

        # 1、先验密码强度。放在建对象之前，省掉一次白算的 Hash。
        try:
            validate_password_strength(request.password, str(request.email))
        except exceptions.InvalidPasswordException as error:
            raise UserAdminDomainError(
                "invalid_password",
                INVALID_PASSWORD_DETAIL,
            ) from error

        # 2、组装记录，密码立即换成 Hash。
        user = UserRecord(
            email=str(request.email),
            hashed_password=self._password_helper.hash(request.password),
            is_active=True,
            is_superuser=request.is_superuser,
            is_verified=True,
            is_environment_admin=False,
        )
        self._session.add(user)
        # 3、靠数据库的唯一约束判重，不先 SELECT 再 INSERT——并发下那种查法必然漏，
        #    两个请求可以都查到「不存在」然后一起插。
        try:
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            raise UserAdminDomainError(
                "user_already_exists",
                "该邮箱的账号已存在。",
            ) from error
        # 4、refresh 取回数据库生成的列（id、created_at 这些）。
        await self._session.refresh(user)
        return user

    async def update_user(
        self,
        user_id: UUID,
        request: UserAdminUpdateRequest,
    ) -> UserRecord:
        """修改普通账号状态，并在降权时撤销全部现有会话。

        两个字段都是可选的：``None`` 表示「这次不改这一项」，不是「改成 False」。所以先算
        出目标状态，再和当前状态比，才能判断这次改动是不是一次降权。

        为什么降权必须连带撤销会话：权限判定发生在登录时，已经签发的 Token 不会因为库里
        的标志位变了就自动失效。不撤销的话，被降权的人只要不退出登录，就还能继续用超管
        功能直到 Token 自己过期。

        Args:
            user_id: 目标账号 id。
            request: 可选的 ``is_active`` 与 ``is_superuser``。

        Returns:
            已更新并刷新的 ``UserRecord``。

        Raises:
            UserAdminDomainError: ``user_not_found``、``environment_admin_protected``，
                或 ``last_superuser_protected``（这次改动会让系统失去最后一个活跃超管）。

        Notes:
            一次 PostgreSQL 写入事务。行锁在第一步就拿，见 ``_get_user_for_update``。
        """

        # 1、锁行取人，并挡掉环境托管账号。
        user = await self._get_user_for_update(user_id)
        await self._ensure_not_environment_managed(user)

        # 2、算目标状态：字段为 None 表示不改这一项，沿用当前值。
        next_active = request.is_active if request.is_active is not None else user.is_active
        next_superuser = (
            request.is_superuser
            if request.is_superuser is not None
            else user.is_superuser
        )
        # 3、判断这次是不是「丢掉活跃超管身份」——原本是活跃超管，改完不再是。
        loses_active_superuser = user.is_active and user.is_superuser and not (
            next_active and next_superuser
        )
        # 4、是降权的话，数一下还剩几个活跃超管。with_for_update 锁住这些行，防止两个
        #    并发请求各自看到「还有 2 个」，然后一起把对方降掉，最后一个都不剩。
        if loses_active_superuser:
            active_superusers = list(
                await self._session.scalars(
                    select(UserRecord.id)
                    .where(
                        UserRecord.is_active.is_(True),
                        UserRecord.is_superuser.is_(True),
                    )
                    .with_for_update()
                )
            )
            if len(active_superusers) <= 1:
                await self._session.rollback()
                raise UserAdminDomainError(
                    "last_superuser_protected",
                    "最后一个活跃超级管理员不能被禁用或降级。",
                )

        # 5、落状态。降权或禁用都要连带撤销会话，理由见上面 docstring。
        user.is_active = next_active
        user.is_superuser = next_superuser
        if loses_active_superuser or not next_active:
            await self._delete_sessions(user.id)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def reset_password(
        self,
        user_id: UUID,
        request: UserAdminPasswordRequest,
    ) -> UserRecord:
        """重置普通账号密码，并撤销该账号全部现有登录 Token。

        改密一定连带撤销会话：改密码的常见动因就是「怀疑这个号被别人用了」，如果旧 Token
        还能继续用，那改密就没起到赶人下线的作用。这里不做「保留当前会话」的例外——操作者
        是超管本人，被改的是别人的号。

        Args:
            user_id: 目标账号 id。
            request: 新密码。

        Returns:
            已更新并刷新的 ``UserRecord``。

        Raises:
            UserAdminDomainError: ``user_not_found``、``environment_admin_protected``
                或 ``invalid_password``。

        Notes:
            一次 PostgreSQL 写入事务。明文密码只用于算 Hash 和校验强度，不落库、不写日志。
        """

        # 1、锁行取人，挡掉环境托管账号。
        user = await self._get_user_for_update(user_id)
        await self._ensure_not_environment_managed(user)
        # 2、验新密码强度。用库里的 email 而不是请求里的，因为这个接口不改邮箱。
        try:
            validate_password_strength(request.password, user.email)
        except exceptions.InvalidPasswordException as error:
            await self._session.rollback()
            raise UserAdminDomainError(
                "invalid_password",
                INVALID_PASSWORD_DETAIL,
            ) from error

        # 3、换 Hash 并清掉全部登录 Token，同一个事务里完成——不能出现「密码改了但旧会话
        #    还活着」的中间态。
        user.hashed_password = self._password_helper.hash(request.password)
        await self._delete_sessions(user.id)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def revoke_sessions(self, user_id: UUID) -> int:
        """撤销目标账号的全部数据库登录 Token，环境管理员也允许主动撤销。

        这里刻意不挡环境管理员：撤销只是让人重新登录一次，不改变任何权限，是安全操作。
        怀疑凭据泄漏时，管理员自己的会话也该能一键清掉。

        Args:
            user_id: 目标账号 id。

        Returns:
            实际删掉的 Token 条数；账号本来就没有登录会话时是 0。

        Raises:
            UserAdminDomainError: ``user_not_found``。

        Notes:
            一次 PostgreSQL 写入事务。只删 access_tokens，不动 users。
        """

        # 1、先确认账号存在（顺带锁行），否则删 0 条和「号不存在」分不清。
        await self._get_user_for_update(user_id)
        # 2、按 user_id 批量删 Token。
        result = await self._session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == user_id)
        )
        await self._session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def _get_user_for_update(self, user_id: UUID) -> UserRecord:
        """取出目标账号并锁住这一行，直到本次事务结束。

        ``with_for_update`` 是这几个写用例的并发基础：不加锁的话，「读出状态 → 判断 →
        写回」之间别的请求可以插进来改同一行，判断就是基于过期数据做的。

        找不到时先 rollback 再抛：调用方拿到的是领域异常，不会再碰 Session，而那把行锁
        必须立刻放掉，不能等请求结束才释放。

        Args:
            user_id: 目标账号 id。

        Returns:
            已加行锁的 ``UserRecord``。

        Raises:
            UserAdminDomainError: ``user_not_found``。

        Notes:
            一次 PostgreSQL ``SELECT ... FOR UPDATE``。
        """

        user = await self._session.scalar(
            select(UserRecord).where(UserRecord.id == user_id).with_for_update()
        )
        if user is None:
            await self._session.rollback()
            raise UserAdminDomainError("user_not_found", "账号不存在。")
        return user

    async def _ensure_not_environment_managed(self, user: UserRecord) -> None:
        """挡住对环境托管管理员的改动。

        环境管理员的邮箱和密码来自服务端配置，每次启动会按配置同步。在这里改它等于改了个
        「下次重启就被覆盖」的值，看起来生效了、实际没有——所以直接拒绝，让人去改配置。

        Args:
            user: 已取出的目标账号。

        Raises:
            UserAdminDomainError: ``environment_admin_protected``。

        Notes:
            纯判断，唯一的 I/O 是拒绝时的 rollback（为了立刻释放上一步拿到的行锁）。
        """

        if user.is_environment_admin:
            await self._session.rollback()
            raise UserAdminDomainError(
                "environment_admin_protected",
                "环境托管的管理员账号必须通过服务端密钥修改。",
            )

    async def _delete_sessions(self, user_id: UUID) -> None:
        """删掉某账号的全部数据库登录 Token，不提交。

        不在这里 commit 是有意的：调用方要把「改状态」和「踢下线」放进同一个事务，中间不能
        出现「已降权但旧会话还活着」的窗口。

        Args:
            user_id: 目标账号 id。

        Notes:
            一次 PostgreSQL ``DELETE``，事务由调用方提交。
        """

        await self._session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == user_id)
        )
