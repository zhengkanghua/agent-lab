"""上游渠道用例与凭据落库的离线验证。

真实 ORM、真实事务跑在内存 SQLite 上（照 ``tests/usage_helpers`` 的做法），不连 PostgreSQL。
这里钉的是工单列出的四条可观察结果：**库里存的是密文**、**明文不进日志 / 异常消息 / repr**、
**编辑时凭据留空不改原凭据**、**保存后要求凭据却没有凭据时被拒**（含「把一条没有凭据的渠道改成
需要凭据的接入类型」这条路径）。

刻意不测的东西：密文的形状与加密算法。断言只到「不是明文、两次保存同一个明文得到不同的值、
不填凭据时不碰已存的值」为止——将来换一种加密方式，这些断言仍然该成立。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import agent_lab.services.llm_credential_cipher as cipher_module
import agent_lab.services.llm_provider_service as provider_service_module
from agent_lab.config.llm_credential import LlmCredentialSettings
from agent_lab.models.llm_provider import LlmProviderRecord
from agent_lab.schemas.llm_providers import (
    LlmProviderCreateRequest,
    LlmProviderUpdateRequest,
)
from agent_lab.services.llm_credential_cipher import (
    CredentialCipher,
    LlmCredentialKeyUnavailableError,
    build_credential_cipher,
)
from agent_lab.services.llm_provider_errors import (
    LlmProviderCredentialRequiredError,
    LlmProviderNotFoundError,
)
from agent_lab.services.llm_provider_service import LlmProviderService

PLAINTEXT = "sk-live-plaintext-must-not-leak"
KEY = Fernet.generate_key().decode()
BASE_URL = "https://api.example.com/v1"


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


@asynccontextmanager
async def provider_database():
    """内存 SQLite 上的真实 ``llm_providers`` 表。

    Yields:
        绑定这个内存库的 ``async_sessionmaker``。
    """

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    LlmProviderRecord.__table__.to_metadata(metadata)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield sessions
    finally:
        await engine.dispose()


def body(**overrides: Any) -> LlmProviderCreateRequest:
    """造一个新增请求；``overrides`` 覆盖单个字段。"""

    return LlmProviderCreateRequest(
        **{
            "name": "主中转站",
            "provider": "openai_compatible",
            "base_url": BASE_URL,
            "credential": PLAINTEXT,
            **overrides,
        }
    )


async def stored_rows(sessions) -> list[LlmProviderRecord]:
    """按主键读出全部行。"""

    async with sessions() as session:
        return list((await session.scalars(select(LlmProviderRecord))).all())


async def create(sessions, **overrides: Any) -> LlmProviderRecord:
    """用真实 Service 新增一条渠道。"""

    async with sessions() as session:
        service = LlmProviderService(session, cipher=CredentialCipher(KEY))
        return await service.create_provider(body(**overrides))


async def update(sessions, provider_id, request: LlmProviderUpdateRequest) -> LlmProviderRecord:
    """用真实 Service 改一条渠道。"""

    async with sessions() as session:
        service = LlmProviderService(session, cipher=CredentialCipher(KEY))
        return await service.update_provider(provider_id, request)


def test_create_stores_ciphertext_and_never_the_plaintext() -> None:
    """落库的是密文：那一列既不是明文，也不含明文。"""

    async def scenario() -> None:
        async with provider_database() as sessions:
            await create(sessions)
            row = (await stored_rows(sessions))[0]

            assert row.name == "主中转站"
            assert row.provider == "openai_compatible"
            assert row.base_url == BASE_URL
            assert row.enabled is True
            assert row.credential_ciphertext is not None
            assert row.credential_ciphertext != PLAINTEXT
            assert PLAINTEXT not in row.credential_ciphertext

    run(scenario())


def test_the_same_plaintext_does_not_land_as_the_same_stored_value() -> None:
    """两条渠道存同一个凭据，落库的两段不相等。

    这一条排掉「把明文或它的摘要直接塞进那一列」这类做法；它不依赖具体的加密方案，
   只要求库里的值不是明文的确定函数。
    """

    async def scenario() -> None:
        async with provider_database() as sessions:
            await create(sessions, name="甲")
            await create(sessions, name="乙")
            first, second = await stored_rows(sessions)

            assert first.credential_ciphertext != second.credential_ciphertext

    run(scenario())


def test_a_created_provider_shows_up_in_the_listing_without_its_credential() -> None:
    """新增之后列表里看得到它，而列表拿到的那一行只有密文，没有明文。"""

    async def scenario() -> None:
        async with provider_database() as sessions:
            created = await create(sessions)
            async with sessions() as session:
                listed = await LlmProviderService(
                    session, cipher=CredentialCipher(KEY)
                ).list_providers()

            assert [row.id for row in listed] == [created.id]
            assert listed[0].credential_ciphertext != PLAINTEXT

    run(scenario())


def test_update_without_credential_keeps_the_stored_value() -> None:
    """编辑时留空凭据 = 不改：密文原样保留，其余字段照改。"""

    async def scenario() -> None:
        async with provider_database() as sessions:
            created = await create(sessions)
            before = created.credential_ciphertext

            updated = await update(
                sessions,
                created.id,
                LlmProviderUpdateRequest(name="改个名", base_url="https://api.example.com/v2"),
            )

            assert updated.credential_ciphertext == before
            assert updated.name == "改个名"
            assert updated.base_url == "https://api.example.com/v2"

    run(scenario())


def test_a_provider_without_credential_cannot_become_credential_requiring() -> None:
    """把没有凭据的渠道改成需要凭据的接入类型、不同时补上凭据 → 保存被拒。

    判定看的是保存之后那一份渠道：所以同一次保存里补上凭据就能通过，而只改接入类型不行，
    被拒之后库里那行也没有被改动（请求失败时事务回滚）。
    """

    async def scenario() -> None:
        async with provider_database() as sessions:
            created = await create(sessions, provider="ollama", credential=None)
            assert created.credential_ciphertext is None

            with pytest.raises(LlmProviderCredentialRequiredError):
                await update(
                    sessions, created.id, LlmProviderUpdateRequest(provider="openai_compatible")
                )
            unchanged = (await stored_rows(sessions))[0]
            assert unchanged.provider == "ollama"
            assert unchanged.credential_ciphertext is None

            fixed = await update(
                sessions,
                created.id,
                LlmProviderUpdateRequest(provider="openai_compatible", credential=PLAINTEXT),
            )
            assert fixed.provider == "openai_compatible"
            assert fixed.credential_ciphertext is not None

    run(scenario())


def test_create_for_a_credential_requiring_provider_needs_a_credential() -> None:
    """新建同类渠道而不填凭据同样被拒，且一行都不落库。"""

    async def scenario() -> None:
        async with provider_database() as sessions:
            with pytest.raises(LlmProviderCredentialRequiredError):
                await create(sessions, credential=None)
            assert await stored_rows(sessions) == []

    run(scenario())


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
def test_a_blank_credential_is_no_credential_on_create(blank, provider) -> None:
    """新建时提交空串 = 没有凭据，而不是「一份空的凭据」。

    这条钉的是一个真出过的洞：新建那条路原来不过滤空串，``credential: ""`` 会被加密存下，
    于是需要凭据的渠道能带着「一份空的凭据」建出来——列表显示「已配置」，实际没有凭据，
    直到第一次选中它才以构造期失败的样子爆出来（spec 0002 明确要拒绝的形状）。

    两种接入类型合起来才说得清：需要凭据的必须被拒、不需要凭据的要真的存成「没有凭据」。
    """

    async def scenario() -> None:
        async with provider_database() as sessions:
            if provider == "openai_compatible":
                with pytest.raises(LlmProviderCredentialRequiredError):
                    await create(sessions, provider=provider, credential=blank)
                assert await stored_rows(sessions) == []
                return

            created = await create(sessions, provider=provider, credential=blank)

            assert created.credential_ciphertext is None
            assert (await stored_rows(sessions))[0].credential_ciphertext is None

    run(scenario())


def test_read_the_unknown_provider_raises_not_found() -> None:
    """读一条不存在的渠道抛领域错误（HTTP 层据此回 404）。"""

    async def scenario() -> None:
        async with provider_database() as sessions:
            async with sessions() as session:
                service = LlmProviderService(session, cipher=CredentialCipher(KEY))
                with pytest.raises(LlmProviderNotFoundError):
                    await service.get_provider(uuid4())

    run(scenario())


def test_a_provider_that_needs_no_credential_never_reads_the_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不需要凭据的渠道（ollama）在没配主密钥时照样保存得了。

    这条是「密钥只在真的要加密时才校验」的正面证据：把它换成会抛错的构造器，保存仍然成功，
    说明那条路径根本没碰主密钥。
    """

    def explode() -> CredentialCipher:
        raise AssertionError("这条保存路径不该读取主密钥")

    monkeypatch.setattr(provider_service_module, "build_credential_cipher", explode)

    async def scenario() -> None:
        async with provider_database() as sessions:
            async with sessions() as session:
                service = LlmProviderService(session)
                record = await service.create_provider(
                    body(provider="ollama", credential=None)
                )
                assert record.credential_ciphertext is None

    run(scenario())


def test_a_missing_master_key_fails_only_the_save_that_needs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主密钥不可用时：要加密的那次保存失败，并且一行都不落库。"""

    def explode() -> CredentialCipher:
        raise LlmCredentialKeyUnavailableError()

    monkeypatch.setattr(provider_service_module, "build_credential_cipher", explode)

    async def scenario() -> None:
        async with provider_database() as sessions:
            async with sessions() as session:
                service = LlmProviderService(session)
                with pytest.raises(LlmCredentialKeyUnavailableError):
                    await service.create_provider(body())
            assert await stored_rows(sessions) == []

    run(scenario())


def test_build_credential_cipher_reads_the_configured_key_and_rejects_broken_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主密钥从 ``LLM_CREDENTIAL_KEY`` 读；留空或不是合法 Fernet 密钥时给同一个异常。"""

    def settings_with(key: str) -> LlmCredentialSettings:
        return LlmCredentialSettings(credential_key=key)

    monkeypatch.setattr(
        cipher_module, "get_llm_credential_settings", lambda: settings_with(KEY)
    )
    assert build_credential_cipher().encrypt(PLAINTEXT) != PLAINTEXT

    for broken in ("", "   ", "not-a-fernet-key"):
        monkeypatch.setattr(
            cipher_module, "get_llm_credential_settings", lambda broken=broken: settings_with(broken)
        )
        with pytest.raises(LlmCredentialKeyUnavailableError) as failure:
            build_credential_cipher()
        # 异常不携带任何文本，因此不可能把密钥片段或底层错误带出去。
        assert str(failure.value) == ""


def test_the_plaintext_never_reaches_logs_repr_or_exception_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """明文只活在请求体到加密之间：日志、DTO 的 repr、异常消息与落库行里都找不到它。"""

    request = body()

    async def scenario() -> None:
        async with provider_database() as sessions:
            created = await create(sessions)
            with pytest.raises(LlmProviderCredentialRequiredError) as failure:
                await create(sessions, credential=None)
            assert PLAINTEXT not in str(failure.value)
            assert PLAINTEXT not in repr(failure.value)
            assert PLAINTEXT not in repr(created)
            assert PLAINTEXT not in repr(await stored_rows(sessions))

    with caplog.at_level(logging.DEBUG):
        run(scenario())

    assert PLAINTEXT not in repr(request)
    assert PLAINTEXT not in caplog.text
