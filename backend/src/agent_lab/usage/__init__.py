"""LLM 调用用量统计子包。

这个子包只做一件事：把「一次模型调用消耗了多少 token」如实记下来，并让本人能查到。它记录，
不拦截——不设额度、不做限制、不算金额。

它刻意与项目领域解耦：只吃 ``contracts.UsageRecord`` 这一个纯数据结构（UUID、数字、字符串
枚举和时间），不导入 ``agent/``、``knowledge/`` 或任何业务 ORM 模型，所以将来把它整体搬成
独立的 LLMOps 服务时，只需要把写入入口从函数调用换成 HTTP 上报（见
``docs/adr/0033-usage-module-independent-of-domain.md``）。

包内分工：``contracts`` 是契约、``collector`` 是采集器、``repository`` 是仓储、``models`` 是
用量表模型、``assembly`` 是装配。用量表挂在自己的元数据上，业务侧的迁移环境看不到它
（见 ``docs/adr/0032-usage-data-in-separate-database.md``）。

术语以仓库根 ``CONTEXT.md`` 为准：**模型调用**是一次请求与响应的往返，**用量**是它消耗的
token 数量及这批数字的来源，**用量记录**是一次调用一条的事实。
"""

from agent_lab.usage.assembly import UsageRuntime
from agent_lab.usage.collector import NoopUsageCollector, QueuedUsageCollector
from agent_lab.usage.contracts import UsageCollector, UsageRecord, UsageSource, UsageStatus
from agent_lab.usage.repository import UsageRepository

__all__ = [
    "NoopUsageCollector",
    "QueuedUsageCollector",
    "UsageCollector",
    "UsageRecord",
    "UsageRepository",
    "UsageRuntime",
    "UsageSource",
    "UsageStatus",
]
