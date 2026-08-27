"""显式启用后验证环境管理员同步与账号管理 Service 的真实 PostgreSQL 行为。"""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from news_vector_service.auth.bootstrap import synchronize_environment_admin
from news_vector_service.config.auth import AuthSettings
from news_vector_service.db.session import engine
from news_vector_service.models.user import AccessTokenRecord, UserRecord
from news_vector_service.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminPasswordRequest,
    UserAdminUpdateRequest,
)
from news_vector_service.services.user_admin_service import (
    UserAdminDomainError,
    UserAdminService,
)


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
                    await service.update_user(
                        second.id,
                        UserAdminUpdateRequest(is_superuser=False),
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
