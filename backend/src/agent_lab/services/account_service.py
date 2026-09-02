"""实现当前登录账号对自己的自助操作：校验旧密码后改密并踢掉其他设备。

本 Service 只读写 PostgreSQL users/access_tokens，且**操作对象永远是调用者自己**：所有公开
方法都要求把「当前登录账号」整个传进来，不接受一个可以指向别人的 id 参数。这是有意的形状
约束——只要接口里存在目标 id，就迟早有人在某条路径上忘记校验它属不属于调用者。改别人的能力
在 ``user_admin_service.py``，那一层要求超级用户。

与 ``UserAdminService.reset_password`` 的两处差别，也是本 Service 存在的理由：
1. 这里必须先校验旧密码。超管改别人密码时没有旧密码可问，本人改自己时必须问，否则
   离开座位没锁屏就等于把账号交出去了。
2. 这里只踢**其他**会话，保留当前这一个。超管改的是别人的号，全踢是对的；本人改自己的
   密码时全踢意味着改完立刻被登出，而他刚刚才证明了自己是账号主人。
"""

from dataclasses import dataclass

from fastapi_users import exceptions
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.auth.manager import validate_password_strength
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.schemas.account import AccountPasswordChangeRequest
from agent_lab.services.user_admin_service import INVALID_PASSWORD_DETAIL


@dataclass(frozen=True, slots=True)
class AccountDomainError(Exception):
    """携带稳定安全代码的账号自助操作预期失败。"""

    code: str
    detail: str


# 旧密码不对的对外文案。刻意不区分「账号不存在」——能走到这里的账号一定存在（它是当前登录的
# 那个），所以这句只有一种含义，不存在枚举风险。
INVALID_CURRENT_PASSWORD_DETAIL = "当前密码不正确。"


class AccountService:
    """以一个请求级 AsyncSession 管理「账号改自己密码」的事务边界。

    生命周期是「一个 HTTP 请求一个实例」，不能跨请求复用：它持有的 Session 就是本次请求的
    事务边界，公开方法自己 commit 或 rollback，调用方不需要再管事务。
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

    async def change_own_password(
        self,
        user: UserRecord,
        current_token: str,
        request: AccountPasswordChangeRequest,
    ) -> None:
        """校验旧密码后替换当前账号的密码，并撤销除当前会话外的全部登录 Token。

        ``user`` 由认证依赖给出，``current_token`` 是本次请求 Cookie 里那个 Token。两者都由
        调用方从认证层取，不从请求体读——请求体可控，读它等于让调用者指定「我是谁」。

        Args:
            user: 当前登录账号，由 ``current_user_token`` 解析得到。
            current_token: 本次请求使用的数据库登录 Token，改密后要保留的就是它。
            request: 旧密码与新密码。

        Raises:
            AccountDomainError: ``environment_admin_protected`` 账号由部署环境托管；
                ``current_password_invalid`` 旧密码不匹配；
                ``invalid_password`` 新密码不合强度策略。

        Notes:
            一次 PostgreSQL 写入事务。两个明文密码只用于校验和算 Hash，不落库、不写日志、
            不进异常消息。
        """

        # 1、锁住自己这一行。锁的意义是「读出 Hash → 比对 → 写回新 Hash」这三步之间不能有
        #    别人改同一行，否则两个并发改密请求会各自基于旧 Hash 判断，后写的那个覆盖前一个。
        locked = await self._session.scalar(
            select(UserRecord).where(UserRecord.id == user.id).with_for_update()
        )
        if locked is None:
            # 认证通过之后账号又被删了。理论上极窄的一个窗口，但把它当成「旧密码不对」比抛
            # 未分类异常好：对调用者来说结果一样（这次改密没成），而且不泄露账号已不存在。
            await self._session.rollback()
            raise AccountDomainError(
                "current_password_invalid",
                INVALID_CURRENT_PASSWORD_DETAIL,
            )

        # 2、环境托管账号一律拒绝，理由与 UserAdminService 里那条同源：它的密码来自服务端
        #    配置，每次启动会按配置同步回去，在这里改等于改了个「下次重启就被覆盖」的值。
        if locked.is_environment_admin:
            await self._session.rollback()
            raise AccountDomainError(
                "environment_admin_protected",
                "环境托管的管理员账号必须通过服务端密钥修改。",
            )

        # 3、验旧密码。verify_and_update 的第二个返回值是「建议升级成的新 Hash」，这里不用：
        #    下一步就要整体替换成新密码的 Hash 了。
        verified, _ = self._password_helper.verify_and_update(
            request.current_password,
            locked.hashed_password,
        )
        if not verified:
            await self._session.rollback()
            raise AccountDomainError(
                "current_password_invalid",
                INVALID_CURRENT_PASSWORD_DETAIL,
            )

        # 4、验新密码强度。用库里的 email，本接口不改邮箱。
        try:
            validate_password_strength(request.new_password, locked.email)
        except exceptions.InvalidPasswordException as error:
            await self._session.rollback()
            raise AccountDomainError(
                "invalid_password",
                INVALID_PASSWORD_DETAIL,
            ) from error

        # 5、换 Hash 并踢掉其他会话，同一个事务里完成——不能出现「密码改了但别的设备还登着」
        #    的中间态，那正是改密要解决的问题。
        locked.hashed_password = self._password_helper.hash(request.new_password)
        await self._session.execute(
            delete(AccessTokenRecord).where(
                AccessTokenRecord.user_id == locked.id,
                AccessTokenRecord.token != current_token,
            )
        )
        await self._session.commit()
