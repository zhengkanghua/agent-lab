"""上游渠道模块的领域异常：自带稳定 code、HTTP 状态码与预写的中文 detail。

照 ``scheduled_task_errors`` 的形状：本模块是叶子模块，不 import 项目内任何模块。异常携带的
是**预写的安全文案**，不是 ``str(error)``，所以把它映射进 HTTP 响应不会带出凭据或数据库文本。
"""


class LlmProviderDomainError(Exception):
    """上游渠道管理预期失败的基类；子类用类属性固定 code、detail 与状态码。"""

    code: str = "llm_provider_error"
    detail: str = "上游渠道操作失败。"
    status_code: int = 409


class LlmProviderNotFoundError(LlmProviderDomainError):
    """按 id 找不到上游渠道（或已被删除；本表只有停用，正常不会走到这里）。"""

    code = "llm_provider_not_found"
    detail = "上游渠道不存在。"
    status_code = 404


class LlmProviderCredentialRequiredError(LlmProviderDomainError):
    """保存之后这条渠道要求凭据、却一份也没有。

    判定看的是保存之后的状态：把一条没有凭据的渠道改成 ``openai_compatible`` 而不在同一
    次保存里补上凭据，以及新建 ``openai_compatible`` 渠道时不填凭据，走的都是这一条。
    """

    code = "llm_provider_credential_required"
    detail = "该接入类型必须配置凭据，请在本次保存里补上凭据。"
    status_code = 422


class LlmProviderInUseAsDefaultError(LlmProviderDomainError):
    """这条渠道下面挂着当前默认模型，不能停用它。

    「目录里只要有可用模型，就恰好有一个默认」这条不变量横跨两张表，一半由模型那一侧守
    （停用默认模型本身被拒），这一条是另一侧：渠道一停，它下面的模型跟着变得不可用，默认
    也就一起没了。要求先把默认换到别的渠道的模型上，而不是替管理员改选一个。
    """

    code = "llm_provider_in_use_as_default"
    detail = "这条渠道下有当前默认模型，请先把默认换到别的渠道的模型再停用它。"
    status_code = 409


__all__ = [
    "LlmProviderCredentialRequiredError",
    "LlmProviderDomainError",
    "LlmProviderInUseAsDefaultError",
    "LlmProviderNotFoundError",
]
