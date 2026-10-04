"""开始运行之前解析「这一轮用哪个模型」：读目录表，给出当轮快照，失败在流开始之前抛出。

**与 ``llm_model_service`` 的分工**：那一个是后台管理用例，持有一个请求级 ``AsyncSession``、
管事务边界；这一个只服务用户提问那条路，持**进程级 session 工厂**、每次开一个短会话。
为什么要短会话：它的调用方 ``POST /agent/chat`` 返回流式响应，``Depends(get_db_session)``
要等流关闭才归还业务连接，一次对话几分钟就是几分钟（见 ADR 0010）。

**解析直接读目录表，不经过任何客户端缓存**：刚停用的模型必须立刻选不到，而不是等缓存过期。
缓存管的是「客户端构造成本」（见 spec 0002 的客户端缓存那一段），不是可用性判定。

**只校验这一轮实际生效的那一份**：请求里给了 id 就用它，没给才用会话行上存的那个；另一份
失效不影响这一轮（会话行上存着一个已停用的旧选择、而这一次请求带了新的，照样放行）。

失败分三种，形状与会话知识库范围失效那一条路一致（走 HTTP 状态码，不是流里的事件）：

- ``LlmModelNotFoundError``：指的那个 id 在目录里没有这一条（404）；
- ``LlmModelUnavailableError``：有这一条，但自身或所属渠道已停用（409）；
- ``NoAvailableLlmModelsError``：会话没选模型、而目录里一个可用模型都没有（409）。
"""

from uuid import UUID

from agent_lab.models.llm_model import LlmModelRecord
from agent_lab.repositories.llm_model_repository import LlmModelRepository
from agent_lab.schemas.llm_models import ResolvedLlmModel
from agent_lab.services.llm_model_errors import (
    LlmModelNotFoundError,
    LlmModelUnavailableError,
    NoAvailableLlmModelsError,
)


def snapshot_of(record: LlmModelRecord) -> ResolvedLlmModel:
    """把目录里一行翻成当轮快照：这个函数是「展示名怎么回落」的**唯一**一处实现。

    留空的展示名在这里落成上游模型名（与 ``api/llm_models.py`` 的选择列表同一规则），
    所以快照里的展示名一定非空，下游（冻结、回放、运行上下文）不必再判一次。
    """

    return ResolvedLlmModel(
        id=record.id,
        display_name=record.display_name or record.upstream_model_name,
        context_window=record.context_window,
    )


class LlmModelSelectionService:
    """以进程级 session 工厂解析当轮模型。

    生命周期是「进程一个实例」：它自己不持连接，每次调用开一个短会话、读完就还。
    """

    def __init__(self, session_factory) -> None:
        """记录 session 工厂，不建连、不查库。"""

        self._session_factory = session_factory

    async def resolve_for_run(self, llm_model_id: UUID | None) -> ResolvedLlmModel:
        """解析这一轮实际生效的模型，返回它的快照。

        Args:
            llm_model_id: 当轮生效的选择；``None`` 表示会话没选过，用目录里标着默认的那一个。

        Returns:
            当轮那个模型的快照（id、展示名、上下文窗口）。

        Raises:
            LlmModelNotFoundError: 给了一个目录里不存在的 id。
            LlmModelUnavailableError: 那一条已停用，或它所属的渠道已停用。
            NoAvailableLlmModelsError: 没给 id，而目录里一个可用模型都没有。

        Notes:
            执行一次 PostgreSQL 读查询，不写任何东西、不调用模型。调用方必须在**开始运行之前**
            await 它：只有这样失败才能变成正常的 HTTP 状态码，而且那一次模型调用一次都不会发出。
        """

        async with self._session_factory() as session:
            models = LlmModelRepository(session)
            if llm_model_id is None:
                # 没选过模型 → 用默认那一个。目录为空、全部停用、或默认自己不可用时都落到
                # 「没得选」这一条上：用户没法自救，文案指向管理员。
                default = await models.default_available_model()
                if default is None:
                    raise NoAvailableLlmModelsError
                return snapshot_of(default)
            row = await models.get_model_with_provider(llm_model_id)
            if row is None:
                raise LlmModelNotFoundError
            record, provider = row
            if not (record.enabled and provider.enabled):
                raise LlmModelUnavailableError
            return snapshot_of(record)

    async def describe_choice(self, llm_model_id: UUID | None) -> ResolvedLlmModel | None:
        """读一个已存的选择此刻在目录里的样子，供回放与选择器显示「原来是 xxx」。

        与 ``resolve_for_run`` 的区别是**它不判可用性、也不抛错**：这一步只是把名字读出来，
        停用的条目照样读得到（那正是失效态要显示的名字）。目录里查不到这一条时返回 ``None``。

        Args:
            llm_model_id: 要读的选择；``None`` 表示这个会话没选过模型。

        Returns:
            这一条此刻的快照；没给 id 或目录里没有这一条时为 ``None``。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方映射成 503。

        Notes:
            执行一次 PostgreSQL 读查询。**不写、不调用模型、不改变任何选择**：选择失效时不静默
            回落成默认模型，只如实告诉用户。
        """

        if llm_model_id is None:
            return None
        async with self._session_factory() as session:
            record = await LlmModelRepository(session).get_model(llm_model_id)
        return None if record is None else snapshot_of(record)


__all__ = ["LlmModelSelectionService", "snapshot_of"]
