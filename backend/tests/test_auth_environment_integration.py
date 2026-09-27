"""显式启用后验证环境托管超级用户同步与账号管理 Service 的真实 PostgreSQL 行为。"""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import httpx

from agent_lab.auth.bootstrap import (
    EnvironmentAdminDeregisteredError,
    synchronize_environment_admin,
)
from agent_lab.config.auth import AuthSettings
from agent_lab.db.session import engine, get_db_session
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminPasswordRequest,
    UserAdminUpdateRequest,
)
from agent_lab.services.user_admin_service import (
    UserAdminDomainError,
    UserAdminService,
)
from tests.app_helpers import create_offline_app


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_AUTH_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_POSTGRES_AUTH_INTEGRATION_TEST=1 to verify environment-admin "
        "synchronization against the configured PostgreSQL database"
    ),
)


def test_environment_admin_sync_and_account_management_transactions() -> None:
    """在可回滚外层事务内验证创建、轮换、保护、改密和撤销会话。"""

    async def verify() -> None:
        suffix = uuid4().hex
        first_email = f"env-first-{suffix}@example.com"
        second_email = f"env-second-{suffix}@example.com"
        regular_email = f"reader-{suffix}@example.com"
        first_password = "integration-first-password"
        rotated_password = "integration-rotated-password"
        second_password = "integration-second-password"
        first_token = f"a{suffix}{'0' * 10}"
        regular_token = f"b{suffix}{'0' * 10}"
        replacement_token = f"c{suffix}{'0' * 10}"

        connection = await engine.connect()
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            first_settings = AuthSettings(
                _env_file=None,
                admin_email=first_email,
                admin_password=SecretStr(first_password),
            )
            created = await synchronize_environment_admin(first_settings, factory)
            assert created.created is True
            assert created.password_changed is True
            assert created.user_id is not None

            async with factory() as session:
                session.add(
                    AccessTokenRecord(
                        token=first_token,
                        user_id=created.user_id,
                    )
                )
                await session.commit()

            unchanged = await synchronize_environment_admin(first_settings, factory)
            assert unchanged.created is False
            assert unchanged.password_changed is False
            async with factory() as session:
                assert await session.get(AccessTokenRecord, first_token) is not None

            rotated_settings = AuthSettings(
                _env_file=None,
                admin_email=first_email,
                admin_password=SecretStr(rotated_password),
            )
            rotated = await synchronize_environment_admin(rotated_settings, factory)
            assert rotated.password_changed is True
            async with factory() as session:
                assert await session.get(AccessTokenRecord, first_token) is None

            second_settings = AuthSettings(
                _env_file=None,
                admin_email=second_email,
                admin_password=SecretStr(second_password),
            )
            moved = await synchronize_environment_admin(second_settings, factory)
            assert moved.created is True
            assert moved.released_previous_managers == 1
            assert moved.user_id is not None

            async with factory() as session:
                first = await session.scalar(
                    select(UserRecord).where(
                        func.lower(UserRecord.email) == first_email.casefold()
                    )
                )
                second = await session.get(UserRecord, moved.user_id)
                assert first is not None
                assert first.is_environment_admin is False
                assert first.is_superuser is True
                assert second is not None
                assert second.is_environment_admin is True

                service = UserAdminService(session)
                with pytest.raises(UserAdminDomainError) as protected:
                    # 调用者用一个与目标不同的账号：撞上自己那条规则的话，这条用例就会
                    # 从环境托管保护变成 account_self_protected，不再验原来那件事。
                    await service.update_user(
                        second.id,
                        UserAdminUpdateRequest(is_active=False),
                        first.id,
                    )
                assert protected.value.code == "environment_admin_protected"

            async with factory() as session:
                service = UserAdminService(session)
                regular = await service.create_user(
                    UserAdminCreateRequest(
                        email=regular_email,
                        password="integration-reader-password",
                    )
                )
                regular_id = regular.id
                with pytest.raises(UserAdminDomainError) as duplicate:
                    await service.create_user(
                        UserAdminCreateRequest(
                            email=regular_email.upper(),
                            password="integration-reader-password",
                        )
                    )
                assert duplicate.value.code == "user_already_exists"

            async with factory() as session:
                session.add(
                    AccessTokenRecord(
                        token=regular_token,
                        user_id=regular_id,
                    )
                )
                await session.commit()
                service = UserAdminService(session)
                await service.reset_password(
                    regular_id,
                    UserAdminPasswordRequest(password="integration-reset-password"),
                )
                assert await session.get(AccessTokenRecord, regular_token) is None

                session.add(
                    AccessTokenRecord(
                        token=replacement_token,
                        user_id=regular_id,
                    )
                )
                await session.commit()
                assert await service.revoke_sessions(regular_id) == 1
                assert await session.get(AccessTokenRecord, replacement_token) is None

            cleared = await synchronize_environment_admin(
                AuthSettings(_env_file=None),  # type: ignore[call-arg]
                factory,
            )
            assert cleared.configured is False
            assert cleared.released_previous_managers == 1
        finally:
            await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_deregistered_account_cannot_log_in_and_startup_refuses_to_revive_it() -> None:
    """真库上把两件事串起来：注销之后登录真的进不来；配置指向它时启动拒绝复活。

    为什么这条必须真库：离线层验不到「注销真的把 ``is_active`` 置成了 false」——那一层把
    用户管理器换成了替身。这里拿一个真账号走完注销，再拿它的邮箱与**正确密码**登录，
    两段就串起来了。驳回登录的文案还要与「密码错误」一模一样：区分两者等于给攻击者
    一个枚举有效邮箱的预言机。

    第二条打的是真正的启动同步入口 ``synchronize_environment_admin``，不是内部辅助函数：
    要保护的性质是「服务起不来」，那只有入口抛错才算数。既不能静默复活（把一个已注销的人
    变成能登录的超管），也不能静默跳过。
    """

    async def verify() -> None:
        suffix = uuid4().hex
        email = f"deregistered-{suffix}@example.com"
        password = "integration-deregistered-password"
        other_operator_id = uuid4()

        connection = await engine.connect()
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            # 1、走 Service 建一个真账号：密码是真 Hash，后面要拿它真登录一次。
            async with factory() as session:
                account = await UserAdminService(session).create_user(
                    UserAdminCreateRequest(email=email, password=password)
                )
                account_id = account.id

            # 2、注销它。调用者用另一个 id：撞上「不能注销自己」的话，这条用例就不再验
            #    它本来要验的事。
            async with factory() as session:
                await UserAdminService(session).delete_user(account_id, other_operator_id)

            # 3、真登录：正确密码也进不来，且与密码错误是同一句话。登录路由走真实的
            #    FastAPI Users Router 与真实数据库 Session，只把 Session 换成外层事务里这个。
            app = create_offline_app()

            async def session_dependency():
                async with factory() as session:
                    yield session

            app.dependency_overrides[get_db_session] = session_dependency
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    correct_password = await client.post(
                        "/auth/login",
                        data={"username": email, "password": password},
                    )
                    wrong_password = await client.post(
                        "/auth/login",
                        data={"username": email, "password": "definitely-not-the-password"},
                    )

            assert correct_password.status_code == 400
            assert correct_password.json() == wrong_password.json()

            # 4、配置里的邮箱指向这个已注销账号：启动同步拒绝启动并说明要人工处理。
            settings = AuthSettings(
                _env_file=None,
                admin_email=email,
                admin_password=SecretStr(password),
            )
            with pytest.raises(EnvironmentAdminDeregisteredError) as refused:
                await synchronize_environment_admin(settings, factory)
            assert "人工处理" in str(refused.value)

            # 5、被拒之后那一行一个字段都没被动过：仍然是已注销、仍然不可用。
            #    这就是「不静默复活」的可观察结果。
            async with factory() as session:
                row = await session.get(UserRecord, account_id)
                assert row is not None
                assert row.deleted_at is not None
                assert row.is_active is False
                assert row.is_environment_admin is False
        finally:
            await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)
