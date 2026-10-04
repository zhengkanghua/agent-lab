"""可用模型模块的领域异常：自带稳定 code、HTTP 状态码与预写的中文 detail。

照 ``llm_provider_errors`` 的形状：本模块是叶子模块，不 import 项目内任何模块。异常携带的是
**预写的安全文案**，不是 ``str(error)``，所以把它映射进 HTTP 响应不会带出数据库文本。

**为什么模型不存在那条不叫 ``llm_model_not_found``**：这个名字已经被 Agent 对话链路占用了
（``api/error_contract.py`` 的 ``AGENT_CHAT_ERROR_RULES``，说的是「上游说没有这个模型」）。
一个 code 对应两件事会让前端文案表和排障都分不清，所以目录这一侧另起一个名字，含义是
「模型目录里没有这一条」。

这一组里有六条是**不变量的守卫**（「目录里只要有可用模型，就恰好有一个默认」），它们不是
同一条错误：管理员要照做的话各不相同——先把另一条设为默认、先启用渠道、先启用模型……
所以一个失败一个 code，前端能逐条给具体的话。

另外两条（``LlmModelUnavailableError`` 与 ``NoAvailableLlmModelsError``）不属于那一组不变量的
守卫，它们服务于**用户提问时那道「开始运行之前解析当轮模型」的门**：一个是「你选的这个现在
用不了、换一个」，一个是「目录里根本没得选、找管理员」。放在同一个模块里，是因为一个 code
只该有一个定义处——两条路读的是同一张目录表。
"""


class LlmModelDomainError(Exception):
    """可用模型管理预期失败的基类；子类用类属性固定 code、detail 与状态码。"""

    code: str = "llm_model_error"
    detail: str = "可用模型操作失败。"
    status_code: int = 409


class LlmModelNotFoundError(LlmModelDomainError):
    """按 id 找不到可用模型（本表只有停用，正常不会走到这里）。"""

    code = "llm_model_entry_not_found"
    detail = "该可用模型不存在，请刷新列表后重试。"
    status_code = 404


class LlmModelNameConflictError(LlmModelDomainError):
    """这条渠道下已经有同名的上游模型。

    判定同时被两条路用到：新建时撞名、以及把一条模型改成已有的名字。库里那条
    ``UniqueConstraint("provider_id", "upstream_model_name")`` 是并发下的兜底。
    """

    code = "llm_model_name_conflict"
    detail = "这条渠道下已经有同名的上游模型，请换一个上游模型名。"
    status_code = 409


class LlmModelProviderDisabledError(LlmModelDomainError):
    """要把模型挂到一条已停用的渠道下面。

    放行它会绕过「默认模型必须可用」：把默认模型改挂到停用渠道下，默认就随着渠道一起不可用，
    而那一刻「会话没选模型」用哪个模型没有任何定义。
    """

    code = "llm_model_provider_disabled"
    detail = "目标上游渠道已停用，请先启用它再把这个模型挂过去。"
    status_code = 409


class LlmModelDefaultNotAvailableError(LlmModelDomainError):
    """给一个当前不可用的模型打默认标记。

    不可用 = 模型自己停了、或它所属的渠道停了（两个条件都要满足才算可用）。放行它就会造出
    「有一个不可用的默认」这种状态。
    """

    code = "llm_model_default_not_available"
    detail = "不能把当前不可用的模型设为默认，请先启用这条模型和它所属的渠道。"
    status_code = 409


class LlmModelDefaultCannotBeClearedError(LlmModelDomainError):
    """请求把当前默认模型的标记置假。

    默认标记只能被「把另一条设为默认」这条路径改写。直接置假会让「有可用模型、却没有默认」
    这个被声明为不可达的状态变成可达（自动补默认只挂在「某条变为可用」这个事件上，这里不会
    被触发），而那一刻「会话没选模型」的提示与「当轮窗口值取哪一条」都没有定义。
    """

    code = "llm_model_default_cannot_be_cleared"
    detail = "默认标记不能直接取消，请先把另一条模型设为默认。"
    status_code = 409


class LlmModelDefaultCannotBeDisabledError(LlmModelDomainError):
    """停用当前默认模型。

    停用等于它不再可用，于是「有可用模型却没有默认」或者「默认不可用」——两者都不是合法状态。
    """

    code = "llm_model_default_cannot_be_disabled"
    detail = "这条模型是当前默认，请先把默认换到另一条模型再停用它。"
    status_code = 409


class LlmModelDefaultConflictError(LlmModelDomainError):
    """两个请求同时设默认，落败的那一方。

    翻自库上那条部分唯一索引抛出的 ``IntegrityError``。不静默重试、也不自动改选一个默认——
    管理员点的是「把这条设为默认」，替他改成别的模型就是替他做了决定。
    """

    code = "llm_model_default_conflict"
    detail = "另一个请求刚刚改过默认模型，请刷新列表后重试。"
    status_code = 409


class LlmModelDefaultConflictError(LlmModelDomainError):
    """两个请求同时设默认，落败的那一方。

    翻自库上那条部分唯一索引抛出的 ``IntegrityError``。不静默重试、也不自动改选一个默认——
    管理员点的是「把这条设为默认」，替他改成别的模型就是替他做了决定。
    """

    code = "llm_model_default_conflict"
    detail = "另一个请求刚刚改过默认模型，请刷新列表后重试。"
    status_code = 409


class LlmModelUnavailableError(LlmModelDomainError):
    """这一轮实际生效的那个模型当前不可用：自身停用，或它所属的渠道停用。

    与上面那几条守不变量的错误不同，它只在**开始运行之前解析当轮模型**那道门上判，
    保存选择时不判（否则同一个失效选择会从保存与提问两处各拿到一条不一样的提示）。
    它复用 ``LlmModelNotFoundError`` 那一条的 404 作对照：4004 是「指的 id 目录里没有」，
    这一条是「有这一条，但它现在不能用」——两者共用一个面向用户的文案（换一个模型）。
    """

    code = "llm_model_unavailable"
    detail = "选中的模型当前不可用，请换一个模型再提问。"
    status_code = 409


class NoAvailableLlmModelsError(LlmModelDomainError):
    """目录里一个可用模型都没有：一条都没配过，或者全部停用了。

    它与 ``LlmModelUnavailableError`` 分开，是因为用户能做的事不一样：那个他自己换一个就行，
    这个他没法自救，只能指向管理员去配。会话里没选模型（存的是空）才会走到这里。
    """

    code = "no_available_llm_models"
    detail = "当前没有可用的模型，请联系管理员配置。"
    status_code = 409


__all__ = [
    "LlmModelDefaultCannotBeClearedError",
    "LlmModelDefaultCannotBeDisabledError",
    "LlmModelDefaultConflictError",
    "LlmModelDefaultNotAvailableError",
    "LlmModelDomainError",
    "LlmModelNameConflictError",
    "LlmModelNotFoundError",
    "LlmModelProviderDisabledError",
    "LlmModelUnavailableError",
    "NoAvailableLlmModelsError",
]
