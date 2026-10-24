"""定义一次运行（run）的请求级上下文。

本模块只声明一个不可变数据类，不读环境、不访问外部服务、不校验业务规则（用户提问的长度上限在
``schemas.agent_chat`` 的 Pydantic 层）。

它存在的理由是让 Agent 能做成**进程级共享**的：编译好的 LangGraph agent 在启动时建一次，
每个请求只通过 ``agent.ainvoke(..., context=AgentContext(...))`` 传入自己的差异部分（本次运行的
知识库范围、当轮模型、账号与会话标识），由中间件在模型调用前读出来。否则这些按请求不同的值
只能靠每请求重新编译一个 agent，那会把编译开销和内存占用乘上并发数。
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.schemas.llm_models import ResolvedLlmModel


@dataclass(frozen=True, slots=True)
class AgentContext:
    """一次运行的请求级参数，由 ``create_agent(context_schema=...)`` 声明并注入。

    做成 frozen 是刻意的：中间件在模型调用前读它，如果某个中间件能改它，后续中间件看到
    的就是被改过的值，「这次运行用的是哪个范围、哪个模型」会变得依赖中间件顺序。

    Attributes:
        user_id: 发起本次运行的账号；``None`` 表示归属未知。用量采集点从当前运行的上下文里
            读它，把每条用量记录归到账号上——查询接口按账号过滤，读不到就谁都查不到。
            有默认值是为了让离线测试与不关心归属的调用点不必构造它；填值由 Agent 对话入口
            负责。
        thread_id: 本次运行所属的会话（``agent_threads`` 的主键）；``None`` 表示归属未知。
            同样供用量记录留一个可追到具体哪次提问的引用，默认空表示未知。
        llm_model: **当轮选定的模型**（id、展示名、上下文窗口），在开始运行之前由
            ``services.llm_model_selection_service`` 读目录表解析出来；``None`` 表示本次运行
            的上下文里没带上它（离线测试与不关心模型的调用点）。它的窗口是历史压缩那条路的
            比例基准（见 spec 0001）；填值由 Agent 对话入口负责，而运行期的取值读的是
            提问消息里冻结的那一份，**不重查目录**（目录可能已经被改过）。
        run_id: 本次运行的标识，由本类在构造时生成。同一次运行的多次模型调用（含中间件内部
            的历史摘要压缩调用）拿到的是同一个值。
    """

    run_id: UUID = field(default_factory=uuid4)
    scope: ResolvedKnowledgeBaseScope | None = None
    llm_model: ResolvedLlmModel | None = None
    user_id: UUID | None = None
    thread_id: UUID | None = None


__all__ = ["AgentContext"]
