"""定义用量查询的对外契约（Pydantic 模型）。

本模块只描述「一条用量明细长什么样」和「一页明细长什么样」，不读数据库、不决定筛选口径
（那是 ``usage.repository.UsageFilter``）。

明细**不带精确总数**：账本表持续增长，精确 COUNT 是白付的代价，而界面不需要「共 N 条」。
需要计数时看汇总接口——那里的调用次数同样受同一组筛选，两个数字天然对得上。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# 明细一页的条数上下限。上限与 ``MAX_THREAD_PAGE_SIZE`` 取同一个数量级，但两者无关联：
# 这里限制的是「一次返回多少条调用记录」。
DEFAULT_USAGE_PAGE_SIZE = 50
MAX_USAGE_PAGE_SIZE = 200


class UsageRecordItem(BaseModel):
    """用量明细里的一行。

    不带账号：这三条接口都只返回当前账号的记录，把账号 id 回给调用方没有信息量。带会话与运行
    的引用是为了满足「一笔消耗能追到具体哪次提问」；带 ``call_id`` 是因为它是这条记录的稳定
    身份，分页核对与排查都靠它。
    """

    call_id: UUID = Field(description="这条用量记录自己的标识；同一条记录跨页不会变。")
    occurred_at: datetime = Field(description="调用进入采集点的 UTC 时刻（带时区）。")
    model_name: str | None = Field(description="这次调用实际使用的模型名；null 表示取不到。")
    status: Literal["completed", "failed"] = Field(
        description="结束方式：completed 为正常跑完，failed 为上游报错、超时或被取消。",
    )
    source: Literal["upstream", "missing"] = Field(
        description="这批数字的来源：upstream 为上游自报（哪怕总量是 0），missing 为上游没报。",
    )
    input_tokens: int = Field(description="上游报的输入 token；已包含缓存命中的部分。")
    output_tokens: int = Field(description="上游报的输出 token。")
    cached_tokens: int | None = Field(
        description="上游报的缓存命中 token；null 表示上游没报这一项，与「报了 0」是两件事。",
    )
    total_tokens: int = Field(description="上游报的合计 token；上游没报时由输入加输出补齐。")
    duration_ms: int = Field(description="这次调用的耗时毫秒数。")
    thread_id: UUID | None = Field(description="所属 Agent 会话；null 表示归属未知。")
    run_id: UUID | None = Field(description="所属运行（一次提问到最终回答）；null 表示归属未知。")

    model_config = ConfigDict(frozen=True, from_attributes=True)


class UsageRecordPage(BaseModel):
    """``GET /usage/records`` 的响应。

    ``has_more`` 由「多取一条」探测得出，不是靠总数算的。offset 分页的已知取舍：一边翻页一边
    有新记录写入时，整页会被往后顶，某一条可能在两页里重复、另一条被跳过；本期不处理这条漂移。
    """

    items: tuple[UsageRecordItem, ...] = Field(
        description="本页明细，按发生时刻倒序、同一时刻按记录主键倒序。",
    )
    has_more: bool = Field(description="是否还有下一页；为 false 表示本页就是最后一页。")

    model_config = ConfigDict(frozen=True)


class UsageSummaryResponse(BaseModel):
    """``GET /usage/summary`` 的响应。

    形状固定：四个 token 合计加调用次数。它**不受明细分页影响**——口径是「筛选范围内的全部
    记录」，不是「当前页」。

    ``cached_tokens`` 是这里唯一可空的字段，因为它是唯一一个「上游可能整段没报」的量：为 null
    表示范围内有记录、但上游一次都没报过缓存；范围内没有记录时它是 0（零消耗必然零缓存，那是
    事实而不是未知）。区分这两种情况与记录层的「缺失不是 0」是同一条原则。
    """

    input_tokens: int = Field(description="范围内输入 token 之和。")
    output_tokens: int = Field(description="范围内输出 token 之和。")
    cached_tokens: int | None = Field(
        description="范围内缓存命中 token 之和；null 表示上游一次都没报过这一项。",
    )
    total_tokens: int = Field(description="范围内合计 token 之和。")
    call_count: int = Field(description="同一筛选条件下的调用次数，等于明细的行数。")

    model_config = ConfigDict(frozen=True)


class UsageErrorResponse(BaseModel):
    """用量查询接口失败时的响应体（固定三字段，与其它链路的错误契约同形）。

    用量库不可用时返回稳定的 503，**不返回空列表、不返回零汇总**：把「查不到」渲染成 0，
    正是这份功能反复拒绝的事——迁移没跑、库被摘掉都会变成「这个月没花钱」。
    """

    code: str = Field(description="稳定机器错误码，前端据此选择提示文案。")
    detail: str = Field(
        min_length=1,
        description="安全中文概述，不含异常文本、连接串或第三方原始响应。",
    )
    retryable: bool = Field(description="是否「不改请求、稍后重试可能成功」。")

    model_config = ConfigDict(frozen=True)


__all__ = [
    "DEFAULT_USAGE_PAGE_SIZE",
    "MAX_USAGE_PAGE_SIZE",
    "UsageErrorResponse",
    "UsageRecordItem",
    "UsageRecordPage",
    "UsageSummaryResponse",
]
