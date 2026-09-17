"""实现当前登录账号读写自己个人偏好的用例。

本 Service 只读写 PostgreSQL ``user_preferences``，且**操作对象永远是调用者自己**：公开方法
要求把「当前登录账号」整个传进来，不接受一个可以指向别人的 id 参数。这是有意的形状约束——
只要接口里存在目标账号 id，就迟早有人在某条路径上忘记校验它属不属于调用者。

它同时也是「建会话时该用哪份提示词」的读取入口：``AgentThreadService`` 不直接碰这张表，
而是经由本 Service 取值，免得「偏好从哪来」出现第二处实现。
"""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.models.user_preference import UserPreferenceRecord
from agent_lab.schemas.document_search import (
    DEFAULT_DOCUMENT_LIMIT,
    DEFAULT_MATCHES_PER_DOCUMENT,
)
from agent_lab.schemas.user_preference import (
    UserPreferenceResponse,
    UserPreferenceUpdateRequest,
)


class UserPreferenceService:
    """以一个请求级 AsyncSession 管理个人偏好读写的事务边界。

    生命周期是「一个 HTTP 请求一个实例」，不能跨请求复用：它持有的 Session 就是本次请求的
    事务边界，``update`` 自己 commit，调用方不需要再管事务。
    """

    def __init__(self, session: AsyncSession) -> None:
        """绑定本次请求的 Session。

        Args:
            session: 请求级 ``AsyncSession``，同时是本 Service 的事务边界。

        Notes:
            不执行 I/O。
        """

        self._session = session

    async def get(self, user_id: UUID) -> UserPreferenceResponse:
        """读取该账号的偏好；没有配过时返回契约默认值。

        没有行**不是错误**，而是「还没配过」这个正常状态。返回默认值而不是 404，调用方
        （设置页、建会话时取提示词）就不必各自处理「查不到」这一支——设置页直接拿到可渲染
        的一份，建会话时拿到的 ``system_prompt`` 是 ``None``，正好表示「使用内置默认提示词」。

        Args:
            user_id: 当前登录账号 id。

        Returns:
            该账号的偏好；未配置时各项取契约默认值。

        Notes:
            一次 PostgreSQL 主键查询，只读不写。
        """

        record = await self._session.get(UserPreferenceRecord, user_id)
        if record is None:
            return UserPreferenceResponse(
                system_prompt=None,
                document_limit=DEFAULT_DOCUMENT_LIMIT,
                matches_per_document=DEFAULT_MATCHES_PER_DOCUMENT,
            )
        return UserPreferenceResponse.model_validate(record)

    async def system_prompt_for(self, user_id: UUID) -> str | None:
        """只取该账号的提示词，供建会话时快照。

        ``None`` 表示没配过，调用方据此让运行使用内置默认提示词。刻意单独开一个只读一列的
        方法而不是复用 ``get``：建会话在 ``AgentThreadService`` 的短事务里，多读两列整数没
        意义，而这个方法的返回值语义（``None`` = 用默认）值得在签名上写明。

        Args:
            user_id: 当前登录账号 id。

        Returns:
            该账号配置的提示词；未配置时为 ``None``。

        Notes:
            一次 PostgreSQL 单列查询。
        """

        return await self._session.scalar(
            select(UserPreferenceRecord.system_prompt).where(
                UserPreferenceRecord.user_id == user_id
            )
        )

    async def replace(
        self,
        user_id: UUID,
        request: UserPreferenceUpdateRequest,
    ) -> UserPreferenceResponse:
        """整体覆盖该账号的偏好，不存在则新建一行。

        用 ``ON CONFLICT DO UPDATE`` 一条语句完成插入或更新，而不是「先查再决定 insert 还是
        update」：后者在并发下会让两个请求都读到「不存在」然后一起插入，其中一个撞主键报错。
        偏好是单行数据、写入很低频，但这条 upsert 并不比两步写法复杂。

        Args:
            user_id: 当前登录账号 id。
            request: 完整的一份偏好，字段已在 schema 层过边界校验与空白归一化。

        Returns:
            落库后重新读出的偏好。

        Raises:
            SQLAlchemyError: 数据库不可用；由调用方映射成稳定 503。

        Notes:
            一次 PostgreSQL 写入事务。``created_at`` 在冲突分支不动，``updated_at`` 用
            ``func.now()`` 推进——它与 ``TimestampMixin`` 的 ``onupdate`` 同义，这里必须
            显式写出，因为 ON CONFLICT 分支不走 ORM 的 onupdate 钩子。
        """

        statement = insert(UserPreferenceRecord).values(
            user_id=user_id,
            system_prompt=request.system_prompt,
            document_limit=request.document_limit,
            matches_per_document=request.matches_per_document,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[UserPreferenceRecord.user_id],
                set_={
                    "system_prompt": statement.excluded.system_prompt,
                    "document_limit": statement.excluded.document_limit,
                    "matches_per_document": statement.excluded.matches_per_document,
                    "updated_at": func.now(),
                },
            )
        )
        await self._session.commit()
        return await self.get(user_id)

    async def delete_for(self, user_id: UUID) -> None:
        """删除该账号的偏好行，不提交。

        供 ``UserAdminService.delete_user`` 在同一事务里调用：拆掉数据库外键之后，删账号
        不会带走这一行，必须由业务层显式处理。不在这里 commit 是有意的——调用方要把
        「删偏好」和「删账号」放进同一个事务，中间不能出现「账号没了、配置还在」的窗口。

        Args:
            user_id: 被删除的账号 id。

        Notes:
            一次 PostgreSQL ``DELETE``，事务由调用方提交。
        """

        await self._session.execute(
            delete(UserPreferenceRecord).where(UserPreferenceRecord.user_id == user_id)
        )


__all__ = ["UserPreferenceService"]
