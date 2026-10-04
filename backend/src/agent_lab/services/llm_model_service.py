"""可用模型的管理用例：挂到渠道下面、维持「目录里恰好有一个默认」。

本层最要紧的一段是**默认模型的维护**，它只在这里实现一次（``ensure_default_model``），
由两条路调用：模型自己的新建/修改，以及渠道被启用之后它下面的模型跟着变得可用
（``LlmProviderService.update_provider`` 调它，service→service，照
``AgentThreadService`` 调 ``UserPreferenceService`` 的先例）。

**不变量：目录里只要有「自身启用且所属渠道也启用」的可用模型，就恰好有一个默认。**
下面几条规则合起来维持它，把能走到「有可用模型但没有默认」或「有一个不可用的默认」的路径
都堵上（编号与 spec 0002 的「实现决策」一一对应）：

1. 新建时不允许给一个不可用的模型打默认标记；
2. 目录里还没有默认时，第一条**变为可用**的模型自动成为默认，三个入口一个都不能漏——
   新建时就是启用的、后来被置为启用的、以及所属渠道被启用之后它跟着变得可用的；
   同一批里有多条同时变为可用时取**最早添加**的那一条（按添加时刻，同一时刻按 id）；
3. 把某个模型设为默认时，它必须当前可用；
4. 默认模型自身、以及它所属的渠道，都不允许被停用（渠道那一半在 ``LlmProviderService``）；
5. 可用模型的所属渠道可以改，但目标渠道必须是启用状态；
6. 请求把当前默认的标记置假 → 拒绝；
7. 两个请求同时设默认 → 同一事务里先清后设，库上的部分唯一索引兜底，落败的一方拿到一个
   明确的冲突错误，不静默重试、也不自动改选一个默认。

校验一律看「**保存之后**这条模型会是什么样」，而不是「请求里给没给」：这样
``{"enabled": true, "is_default": true}`` 一次提交能过，而只把默认打上、模型还是停着的被拒——
后一种正是第 1 条要挡的形状。这与 ``LlmProviderService`` 判凭据的口径一致。
"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from agent_lab.models.llm_model import LlmModelRecord
from agent_lab.models.llm_provider import LlmProviderRecord
from agent_lab.repositories.llm_model_repository import LlmModelRepository
from agent_lab.repositories.llm_provider_repository import LlmProviderRepository
from agent_lab.schemas.llm_models import LlmModelCreateRequest, LlmModelUpdateRequest
from agent_lab.services.llm_model_errors import (
    LlmModelDefaultCannotBeClearedError,
    LlmModelDefaultCannotBeDisabledError,
    LlmModelDefaultConflictError,
    LlmModelDefaultNotAvailableError,
    LlmModelNameConflictError,
    LlmModelNotFoundError,
    LlmModelProviderDisabledError,
)
from agent_lab.services.llm_provider_errors import LlmProviderNotFoundError


@dataclass(frozen=True, slots=True)
class ModelView:
    """一条可用模型连它所属渠道的展示名与启用位。

    这两项在模型自己的行上不存在，要 join ``llm_providers`` 才拿得到（可用性是查出来的，
    不是存出来的），所以它们跟模型行一起往下走，路由层不必再查一次渠道。

    Attributes:
        model: 可用模型那一行。
        provider_name: 所属渠道的展示名称。
        provider_enabled: 所属渠道是否启用；选择列表里的条目必然是 ``True``（查询已经筛过）。
    """

    model: LlmModelRecord
    provider_name: str
    provider_enabled: bool

    @classmethod
    def of(cls, model: LlmModelRecord, provider: LlmProviderRecord) -> "ModelView":
        """拼一份视图；渠道那一行已经读过时用这个，不必再查一次。"""

        return cls(model=model, provider_name=provider.name, provider_enabled=provider.enabled)


class LlmModelService:
    """以一个请求级 AsyncSession 管理可用模型的事务边界。

    生命周期是「一个 HTTP 请求一个实例」：它持有的 Session 就是本次请求的事务边界，每个公开
    方法自己 commit，调用方不需要再管事务。
    """

    def __init__(self, session):
        self._session = session
        self._models = LlmModelRepository(session)
        self._providers = LlmProviderRepository(session)

    async def list_models(self):
        """后台管理列表：全部模型（含停用的），连所属渠道的展示名与启用位一起返回。"""

        return [ModelView(*row) for row in await self._models.list_models()]

    async def list_available_models(self):
        """用户的选择列表：只回「自身启用 **且** 所属渠道也启用」的模型，连渠道展示名。"""

        return [ModelView(*row, provider_enabled=True) for row in await self._models.list_available_models()]

    async def create_model(self, request: LlmModelCreateRequest):
        """新增一条可用模型。

        Raises:
            LlmProviderNotFoundError: 请求里的所属渠道不存在。
            LlmModelDefaultNotAvailableError: 要给一个不启用的模型打默认标记（所属渠道停用同理）。
            LlmModelNameConflictError: 这条渠道下已经有同名的上游模型。
            LlmModelDefaultConflictError: 另一个请求刚刚改过默认模型。
        """

        # 1、先按保存之后的状态校验：默认标记只允许打在一个可用的模型上，不通过就一行都不落库
        provider = await self._require_provider(request.provider_id)
        if request.is_default:
            self._require_available_for_default(enabled=request.enabled, provider=provider)
        # 2、插入这一行。**默认标记不走这一步**（这里永远插 False）：那个部分唯一索引要挡的是
        #    「两个请求同时设默认」，把默认塞进 INSERT 就会让这里的 IntegrityError 分不清是
        #    撞了上游模型名还是撞了默认——两条失败要给管理员的话不一样，所以分开写、各自明确。
        try:
            record = await self._models.create_model(
                provider_id=request.provider_id,
                upstream_model_name=request.upstream_model_name,
                display_name=request.display_name,
                context_window=request.context_window,
                enabled=request.enabled,
            )
        except IntegrityError:
            raise LlmModelNameConflictError() from None
        # 3、默认这一半：明确要求打默认的走「设为默认」那条路径，否则补一次默认
        #    （新建时就是启用的模型可能正是目录里第一条变为可用的）。
        if request.is_default:
            await self._set_default(record)
        else:
            await self.ensure_default_model()
        await self._models.refresh(record)
        return ModelView.of(record, provider)

    async def update_model(self, model_id, request: LlmModelUpdateRequest):
        """按请求改这条模型，然后按**保存之后**的状态校验那几条不变量。

        校验不通过时不提交：``get_db_session`` 在请求失败时回滚，未提交的字段改动不会落库。

        Raises:
            LlmModelNotFoundError: 按 id 找不到这条模型。
            LlmProviderNotFoundError: 请求要把模型改挂到一条不存在的渠道。
            LlmModelProviderDisabledError: 请求要把模型改挂到一条已停用的渠道。
            LlmModelDefaultCannotBeClearedError: 请求把当前默认的标记置假。
            LlmModelDefaultCannotBeDisabledError: 请求停用当前默认模型。
            LlmModelDefaultNotAvailableError: 请求把默认打在保存之后仍不可用的模型上。
            LlmModelNameConflictError: 改成上游模型名之后与同渠道的其它条目撞名。
            LlmModelDefaultConflictError: 另一个请求刚刚改过默认模型。
        """

        # 1、读出来并加行锁；随后两次读写都在同一条锁内
        record = await self._models.lock_model(model_id)
        if record is None:
            raise LlmModelNotFoundError()
        # 2、算出保存之后的这份状态：默认标记、启用位、所属渠道都用它来判
        provider_id = record.provider_id if request.provider_id is None else request.provider_id
        provider = await self._require_provider(provider_id)
        was_default = record.is_default
        enabled = record.enabled if request.enabled is None else request.enabled
        is_default = was_default if request.is_default is None else request.is_default
        self._require_default_flags_kept(
            was_default=was_default, is_default=is_default, enabled=enabled
        )
        if provider_id != record.provider_id and not provider.enabled:
            raise LlmModelProviderDisabledError()
        if is_default:
            self._require_available_for_default(enabled=enabled, provider=provider)
        # 3、写回这一行的字段；上游模型名撞了同名约束时翻成明确的冲突错误
        if request.upstream_model_name is not None:
            record.upstream_model_name = request.upstream_model_name
        # 展示名要能改回「没有」：请求里带了这个字段就按它写（空串已在校验里折成 None），
        # 没带就不碰——两者只靠 None 分不开，所以看的是 model_fields_set。
        if "display_name" in request.model_fields_set:
            record.display_name = request.display_name
        if request.context_window is not None:
            record.context_window = request.context_window
        record.provider_id = provider_id
        record.enabled = enabled
        try:
            await self._models.commit()
        except IntegrityError:
            raise LlmModelNameConflictError() from None
        # 4、默认这一半：这次改动可能让这条模型「变为可用」，目录里若还没有默认就补上
        if is_default and not was_default:
            await self._set_default(record)
        elif not is_default:
            await self.ensure_default_model()
        await self._models.refresh(record)
        return ModelView.of(record, provider)

    async def ensure_default_model(self) -> None:
        """目录里还没有默认、但已经有可用模型时，把**最早添加**的那一条标成默认。

        规则只在这里实现一次；它被三条入口共用（见模块 docstring 第 2 条），其中「所属渠道被
        启用之后模型跟着变得可用」那条路由 ``LlmProviderService.update_provider`` 调这个公开
        方法进来——放在两处各写一遍，两边迟早会对「同一批多条同时变为可用时选哪一条」给出
        不同答案。

        没有可用模型、或已经有默认时什么都不做。

        Raises:
            LlmModelDefaultConflictError: 另一个请求在同一瞬间把默认设到了别的条目上；这个 commit
                失败会把调用方那一笔一起回滚，所以必须让调用方看到一次明确的冲突。
        """

        if await self._models.get_default_model() is not None:
            return
        candidate = await self._models.earliest_available_model()
        if candidate is None:
            return
        candidate.is_default = True
        try:
            await self._models.commit()
        except IntegrityError:
            # 另一个请求在同一瞬间把默认设到了**别的**条目上，库上那条部分唯一索引把这一方拦下来。
            # 这里必须给一个明确的冲突，不能吞：这次 commit 失败会把**调用方那一笔一起回滚**
            # （「所属渠道被启用」那条路就是这样，吞掉等于「点了启用却没启用」）。所以与显式
            # 设默认走同一个错误，让管理员刷新后重试。
            raise LlmModelDefaultConflictError() from None

    async def provider_has_default_model(self, provider_id) -> bool:
        """这条渠道下面有没有当前默认模型：渠道停用前必须问一次（见第 4 条）。"""

        return await self._models.provider_has_default_model(provider_id)

    async def _set_default(self, record) -> None:
        """把这一条设为默认：同一事务里先清掉别的默认、再把这行置真。

        Raises:
            LlmModelDefaultConflictError: 另一个请求在这一刻也设了默认（库上那条部分唯一索引
                在提交时把落败的这一方拦下来了）。
        """

        try:
            await self._models.set_default(record)
        except IntegrityError:
            raise LlmModelDefaultConflictError() from None

    async def _require_provider(self, provider_id):
        """取所属渠道；不存在时抛领域错误（模型挂不上一条不存在的渠道）。

        这里**不看渠道启不启用**：一条停用渠道下面的模型仍然要能改名字、改窗口；「目标渠道
        必须启用」只在两处判——改挂渠道时（第 5 条）与要打默认标记时（第 1、3 条）。
        """

        provider = await self._providers.get_provider(provider_id)
        if provider is None:
            raise LlmProviderNotFoundError()
        return provider

    def _require_available_for_default(self, *, enabled: bool, provider) -> None:
        """要打默认标记的那条模型，保存之后必须真的可用：自身启用 + 所属渠道启用。"""

        if not (enabled and provider.enabled):
            raise LlmModelDefaultNotAvailableError()

    def _require_default_flags_kept(
        self, *, was_default: bool, is_default: bool, enabled: bool
    ) -> None:
        """当前默认模型的标记与启用位都不许被请求直接改掉（第 4、6 条）。

        默认标记只能被「把另一条设为默认」这条路径改写；默认模型也不能自己停用——两者都会
        造出「有可用模型却没有默认」或「默认不可用」，而那一刻会话没选模型时该怎么办没有定义。
        """

        if was_default and not is_default:
            raise LlmModelDefaultCannotBeClearedError()
        if was_default and not enabled:
            raise LlmModelDefaultCannotBeDisabledError()
