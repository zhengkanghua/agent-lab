"""用量记录的纯数据契约与采集器接口。

本模块是整个用量子包与外界之间唯一的形状约定：Agent 侧把一次模型调用的事实装成
``UsageRecord`` 交给采集器，仓储侧把它按列原样落库。它只有 UUID、数字、字符串枚举和时间，
没有 ORM 对象、没有 LangChain 消息、不知道「会话」「运行」是什么——只知道那是几个不透明引用。
正因如此，这个子包可以整块搬走（见 ``docs/adr/0033-usage-module-independent-of-domain.md``）。

字段顺序与 ``docs/specs/0001-llm-usage-statistics.md`` 的「写入契约」一致，便于逐项对照。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4


class UsageStatus(StrEnum):
    """一次模型调用的结束方式。

    只有两种：正常跑完是 ``COMPLETED``；上游报错、超时、被取消都归 ``FAILED``。它描述的是
    「这次调用怎么结束的」，不是「这次调用值不值得记」——进了采集点就记一条，状态只是字段
    不同。
    """

    COMPLETED = "completed"
    FAILED = "failed"


class UsageSource(StrEnum):
    """这批 token 数字的来源。

    判据只有「拿到没拿到上游的用量对象」：拿到就是 ``UPSTREAM``（哪怕报回来的总量都是 0），
    没拿到就是 ``MISSING``。它存在的意义正是把「上游没报」与「上游报了 0」分开——两者都写
    0 的时候，只有这一列能把它们区分开，否则「缺失」会被当成「免费」。
    """

    UPSTREAM = "upstream"
    MISSING = "missing"


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageRecord:
    """一次模型调用的用量事实，不可变，一次调用一条。

    ``kw_only`` 是刻意的：十三个字段里有好几个同类型的数字，位置传参会把
    ``input_tokens`` 和 ``output_tokens`` 这类相邻字段写反，而写反了不会报错。

    Attributes:
        call_id: 这条记录自己的标识。默认在**受理这一条记录时**现取一个新 ``uuid4``：
            生产是多个 API 进程、每个进程各有自己的队列，用进程内序号、或者在采集器构造时
            算一次，都会让不同进程撞上表上的唯一约束，而写入失败的处置方式是丢弃，结果是
            账本静默少数。它挡的是「同一条记录被重复写入」，不挡「同一次调用被重新受理」。
        user_id: 这次调用归属的账号。``None`` 表示归属未知——三条查询接口都按账号过滤，
            所以这种记录谁都查不到，采集点会为此留一条日志。**不传 ORM 对象**：传了就搬不走。
        thread_id: 这次调用所属的会话（Agent 会话）。不透明引用，含义由业务侧定义。
        run_id: 这次调用所属的运行（一次提问到最终回答之间的整段执行）。不透明引用。
        model_name: 实际使用的模型名。主模型、备用模型各自成条，靠这一列分辨。
        input_tokens: 上游报的输入 token。它**已经包含**缓存命中的部分，所以它、输出、缓存
            三者不能相加当成消耗。
        output_tokens: 上游报的输出 token。
        cached_tokens: 上游报的缓存命中 token（用量对象输入明细里键名以 ``cache_read`` 结尾
            的那一项）。``None`` 表示上游没报这一项——**记缺失，不记 0**，否则会和「报了 0
            缓存」混在一起，正好是 ``UsageSource`` 要区分的那件事。
        total_tokens: 上游报的合计 token；上游没报合计时由输入加输出补齐。
        duration_ms: 从进入包装层到这次调用结束之间的单调时钟差值毫秒数。
        status: 结束方式，见 ``UsageStatus``。
        source: 这批数字的来源，见 ``UsageSource``。
        occurred_at: 调用进入包装层的 UTC 时刻。带时区，落库后也按 UTC 解释。
    """

    user_id: UUID | None
    thread_id: UUID | None
    run_id: UUID | None
    model_name: str | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    total_tokens: int
    duration_ms: int
    status: UsageStatus
    source: UsageSource
    occurred_at: datetime
    call_id: UUID = field(default_factory=uuid4)


class UsageCollector(Protocol):
    """受理用量记录的接口，是采集点与持久化之间唯一的缝。

    实现必须**同步、立即返回、从不抛异常**：调用方是模型包装层，它跑在对话链路上，采集
    失败绝不能变成对话失败。做不到这一点时应当在实现内部把错误降级成日志。

    之所以是同步方法而不是 ``async``：包装层在 ``except BaseException`` 里也要能调用它
    （取消路径），而那里不适合引入 ``await``；实现内部该异步的地方（批量落库）自己在后台
    任务里做。
    """

    def record(self, record: UsageRecord) -> None:
        """受理一条记录。

        Args:
            record: 已经装好的一次调用事实；实现不得修改它。
        """

        ...

    async def drain(self) -> None:
        """把已受理但还没落库的写入收干。

        调用时机是关停（或测试需要确定性地观察库里的结果时），**不是**包装层：包装层只在调用
        现场调 ``record``。没有这个口子的话，快速重启会把刚受理、还没跑完的写入连同连接一起
        丢掉，而且不报错——这正是本项目参考的开源网关踩过的坑
        （见 ``docs/adr/0033-usage-module-independent-of-domain.md``）。
        """

        ...
