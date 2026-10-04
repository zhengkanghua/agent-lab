"""上游渠道的管理用例：校验凭据要求、把明文加密后落库、只向外给出脱敏视图。

两条业务规则都在这一层，且都必须按「**保存之后**这条渠道会变成什么」判断，不能只看请求里
给没给：

1. 编辑时凭据留空 = 不改凭据（请求里没给凭据时不动已存的密文）；
2. 一条渠道保存后如果要求凭据而一份也没有，保存被拒——它涵盖新建、以及「把一条没有凭据的
   渠道改成需要凭据的接入类型却没同时补上凭据」这两种形状。

渠道的停与启用还牵着挂在上面的可用模型，所以这一层还有两件事（不变量「目录里只要有可用模型
就恰好有一个默认」的渠道那一半，见 ``LlmModelService`` 的模块说明）：

3. 停用一条渠道前先看它下面有没有当前默认模型，有则拒绝——渠道一停，那条默认就不再可用；
4. 启用一条渠道时调一次默认维护：它下面的启用模型跟着变得可用，目录里若还没有默认就补上。
   这段规则只有一份实现，在 ``LlmModelService.ensure_default_model``，这里只调它。

明文只在 ``_encrypt`` 的入参与返回值之间存活：它不会被写进日志、异常消息或任何对象的
``__repr__``（服务不持有它，DTO 用 ``SecretStr`` 包住）。
"""

from agent_lab.config.llm import LlmProvider
from agent_lab.repositories.llm_provider_repository import LlmProviderRepository
from agent_lab.schemas.llm_providers import (
    LlmProviderCreateRequest,
    LlmProviderUpdateRequest,
)
from agent_lab.services.llm_credential_cipher import (
    CredentialCipher,
    build_credential_cipher,
)
from agent_lab.services.llm_model_service import LlmModelService
from agent_lab.services.llm_provider_errors import (
    LlmProviderCredentialRequiredError,
    LlmProviderInUseAsDefaultError,
    LlmProviderNotFoundError,
)


class LlmProviderService:
    """以一个请求级 AsyncSession 管理上游渠道的事务边界。

    生命周期是「一个 HTTP 请求一个实例」：它持有的 Session 就是本次请求的事务边界，每个公开
    方法自己 commit，调用方不需要再管事务。
    """

    def __init__(self, session, cipher: CredentialCipher | None = None):
        """绑定本次请求的 Session。

        Args:
            session: 请求级 ``AsyncSession``，同时是本 Service 的事务边界。
            cipher: 现成的加解密器；省略时在**第一次真的需要加密**时按环境构造（见
                ``_credential_cipher``）。测试注入它是为了不依赖环境变量。
        """

        self._session = session
        self._repository = LlmProviderRepository(session)
        self._cipher = cipher

    async def list_providers(self):
        """返回全部渠道配置（含已停用的），供后台管理列表使用。"""

        return list(await self._repository.list_providers())

    async def get_provider(self, provider_id):
        """按 id 读取一条渠道；不存在时抛 ``LlmProviderNotFoundError``。"""

        record = await self._repository.get_provider(provider_id)
        if record is None:
            raise LlmProviderNotFoundError()
        return record

    async def create_provider(self, request: LlmProviderCreateRequest):
        """新增一条渠道，凭据加密后落库。

        Raises:
            LlmProviderCredentialRequiredError: 所选接入类型要求凭据而请求里没有。
            LlmCredentialKeyUnavailableError: 需要加密但主密钥没配或不是合法 Fernet 密钥。
        """

        ciphertext = self._encrypt(request.credential)
        self._require_credential(request.provider.value, ciphertext)
        record = await self._repository.create_provider(
            name=request.name,
            provider=request.provider.value,
            base_url=str(request.base_url),
            credential_ciphertext=ciphertext,
            enabled=request.enabled,
        )
        await self._repository.refresh(record)
        return record

    async def update_provider(self, provider_id, request: LlmProviderUpdateRequest):
        """按请求改这条渠道，然后按**保存之后**的状态校验凭据要求与默认模型。

        校验不通过时不提交：``get_db_session`` 在请求失败时回滚，未提交的字段改动不会落库。

        Raises:
            LlmProviderInUseAsDefaultError: 请求停用这条渠道，而它下面挂着当前默认模型。
        """

        record = await self._repository.lock_provider(provider_id)
        if record is None:
            raise LlmProviderNotFoundError()
        if request.name is not None:
            record.name = request.name
        if request.provider is not None:
            record.provider = request.provider.value
        if request.base_url is not None:
            record.base_url = str(request.base_url)
        # 启停渠道必须真的落到那一列上：它是挂在上面的模型的可用性的另一半，只改模型自己的
        # 启用位表达不了「整条渠道停用」。
        if request.enabled is not None:
            record.enabled = request.enabled
        # 留空 = 不改：请求里没有凭据时连已存的密文都不碰，管理员改地址不必重新输入密钥。
        if request.credential is not None:
            record.credential_ciphertext = self._encrypt(request.credential)
        self._require_credential(record.provider, record.credential_ciphertext)
        # 「目录里恰好有一个默认」这条不变量横跨两张表，这里是渠道这一半的两件事。
        # 默认维护那段规则只有一份实现，它在 LlmModelService；这里只调它，不重写一遍。
        models = LlmModelService(self._session)
        if request.enabled is False and await models.provider_has_default_model(provider_id):
            # 渠道一停，它下面的模型跟着不可用，那条默认也就一起没了：拒绝，要求先换默认。
            raise LlmProviderInUseAsDefaultError()
        if request.enabled is True:
            # 「所属渠道被启用之后模型跟着变得可用」是「第一条变为可用的模型自动成为默认」的第三个
            # 入口（前两个是新建时就启用、后来被置为启用）。渠道那一笔此刻还没提交，查询会自动
            # 把它 flush 进去，所以两边在同一次提交里落库，不会露出「有可用模型却没有默认」那一瞬。
            await models.ensure_default_model()
        await self._repository.commit()
        await self._repository.refresh(record)
        return record

    def _credential_cipher(self) -> CredentialCipher:
        """按需构造加解密器，并留在本 Service 里复用。

        密钥只在这一刻才被读取和校验：没有凭据要加密的请求（例如一条 ollama 渠道）不会碰
        它，所以没配主密钥的部署仍然改得动这类渠道。
        """

        if self._cipher is None:
            self._cipher = build_credential_cipher()
        return self._cipher

    def _encrypt(self, credential) -> str | None:
        """把请求里的凭据明文加密成落库用密文；没有凭据时返回 ``None``。"""

        if credential is None:
            return None
        return self._credential_cipher().encrypt(credential.get_secret_value())

    def _require_credential(self, provider: str, ciphertext: str | None) -> None:
        """这条渠道保存后会要求凭据、却没有凭据时拒绝保存。

        判定依据是保存后的 ``provider`` 与保存后的密文，而不是请求里有没有凭据：先有凭据、
        后来改接入类型的路径与「新建同类渠道」走的是同一条判断。
        """

        if provider == LlmProvider.OPENAI_COMPATIBLE and ciphertext is None:
            raise LlmProviderCredentialRequiredError()
