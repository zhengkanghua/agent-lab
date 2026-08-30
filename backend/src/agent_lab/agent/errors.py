"""定义 Agent 层自己的分类异常。

本模块只声明异常类型，不含文案、不含状态码、不写日志——「异常类型 → 对外 code/detail/
retryable」的映射统一在 ``api.error_contract`` 的有序表里，这样对外契约只有一处可改。

为什么不复用检索层的异常：检索层的异常描述的是「Embedding 或 Qdrant 出了什么问题」，
Agent 层的失败原因不同（模型不可用、会话记忆不可用、请求超出上限）。混用会让错误表里
一个异常类型对应两种含义，映射就只能靠调用位置猜。
"""


class AgentError(RuntimeError):
    """Agent 层所有分类失败的基类。

    存在的意义是让路由能用一个 ``except AgentError`` 兜住本层的已分类失败，同时保证
    错误表里一定查得到对应规则、不会掉进 500 兜底。
    """


class AgentRuntimeUnavailableError(AgentError):
    """ASGI lifespan 尚未提供进程级 Agent Runtime。

    触发场景和 ``VectorSearchRuntimeUnavailableError`` 一样：应用没走 lifespan 启动，
    或 Runtime 构造失败。不在请求路径里临时补建——宁可明确 503。
    """


class AgentCheckpointerUnavailableError(AgentError):
    """会话记忆的 PostgreSQL 连接池不可用。

    和业务库不可用是两回事：业务库挂了检索也做不了，而这里只是「记不住历史」。但仍然
    对外报错而不是降级成无记忆模式——静默丢历史会让用户以为模型失忆，比明确报错更难查。
    """


class ModelResponseInvalidError(AgentError):
    """模型返回了结构上无法使用的内容。

    比如声称要调用一个不存在的工具、或工具参数无法通过 args_schema 校验。属于上游行为
    异常，重试通常无用（同样的提示词大概率再犯），所以映射成不可重试。
    """


__all__ = [
    "AgentCheckpointerUnavailableError",
    "AgentError",
    "AgentRuntimeUnavailableError",
    "ModelResponseInvalidError",
]
