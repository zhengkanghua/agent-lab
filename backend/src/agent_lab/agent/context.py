"""定义一次运行（run）的请求级上下文。

本模块只声明一个不可变数据类，不读环境、不访问外部服务、不校验业务规则（长度上限由
``schemas.agent_chat`` 的 Pydantic 层挡在更前面）。

它存在的理由是让 Agent 能做成**进程级共享**的：编译好的 LangGraph agent 在启动时建一次，
每个请求只通过 ``agent.ainvoke(..., context=AgentContext(...))`` 传入自己的差异部分，
由 ``dynamic_prompt`` 中间件在模型调用前读出来。否则「每个用户用自己的系统提示词」就只能
靠每请求重新编译一个 agent，那会把编译开销和内存占用乘上并发数。
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentContext:
    """一次运行的请求级参数，由 ``create_agent(context_schema=...)`` 声明并注入。

    做成 frozen 是刻意的：中间件在模型调用前读它，如果某个中间件能改它，后续中间件看到
    的就是被改过的值，「这次运行用的是哪个提示词」会变得依赖中间件顺序。

    Attributes:
        system_prompt: 本次运行使用的系统提示词；``None`` 表示用
            ``prompts.DEFAULT_SYSTEM_PROMPT``。非 ``None`` 时**整体替换**默认提示词，
            不拼接，理由见 ``agent.prompts`` 的模块说明。
    """

    system_prompt: str | None = None


__all__ = ["AgentContext"]
