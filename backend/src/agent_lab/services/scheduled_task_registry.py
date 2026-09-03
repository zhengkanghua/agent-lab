"""定时任务类型注册表：``task_type`` 字符串到参数 schema 与描述的映射。

本模块是叶子模块：只声明「有哪些任务类型、各自的参数长什么样」，不执行任何任务、
不 import 流水线代码。任务类型清单由代码注册而不是数据库数据——新增类型等于改代码
（加参数模型、在调度器执行分发处加一个分支），这是刻意约束：任务执行入口必须是
被审查过的代码，不能靠往表里插一行就凭空多出一种写操作。

参数校验发生在两处：管理 API 写入时（把任意 JSON 收敛成该类型的规范形状）和任务
执行前（防御性重验，配置可能被绕过 API 直接改库）。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_lab.pipeline.limits import (
    DEFAULT_INDEX_BATCH_SIZE,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_STALE_AFTER_MINUTES,
    MAX_INDEX_BATCH_SIZE,
    MAX_LIMIT_PER_SOURCE,
    MAX_STALE_AFTER_MINUTES,
)


class FreshRssSyncTaskParams(BaseModel):
    """``freshrss_sync`` 任务的执行参数：FreshRSS 增量同步进 PostgreSQL。

    默认值与手动流水线接口的默认值一致；上限沿用共享的 ``pipeline.limits`` 常量，
    保证定时执行和手动执行的有界语义完全相同。
    """

    model_config = ConfigDict(extra="forbid")

    limit_per_source: int = Field(
        default=DEFAULT_LIMIT_PER_SOURCE,
        ge=1,
        le=MAX_LIMIT_PER_SOURCE,
        description="每个白名单来源单轮最多同步的新闻条数。",
    )


class IndexPendingTaskParams(BaseModel):
    """``index_pending`` 任务的执行参数：PostgreSQL 待索引文档写进 Qdrant。"""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(
        default=DEFAULT_INDEX_BATCH_SIZE,
        ge=1,
        le=MAX_INDEX_BATCH_SIZE,
        description="单轮最多领取并索引的待处理文档数。",
    )
    stale_after_minutes: int = Field(
        default=DEFAULT_STALE_AFTER_MINUTES,
        ge=1,
        le=MAX_STALE_AFTER_MINUTES,
        description="processing 状态超过该分钟数后视为卡死并回收重排。",
    )


class TaskTypeSpec:
    """一个任务类型的注册项：类型名、给人看的描述和参数模型。

    描述会进入 OpenAPI（管理端下拉框的文案来源）；参数模型同时承担写入校验和
    执行前重验。``validate_params`` 返回**规范化后**的参数 dict：缺省字段补默认值、
    未知字段直接拒绝（宁可让提交者当场看到 422，也不静默丢字段制造「配了没生效」的
    假象），保证库里存的形状总是可执行的。
    """

    __slots__ = ("description", "params_model", "task_type")

    def __init__(
        self,
        *,
        task_type: str,
        description: str,
        params_model: type[BaseModel],
    ) -> None:
        """绑定类型名、描述与参数模型，不做任何 I/O。"""

        self.task_type = task_type
        self.description = description
        self.params_model = params_model

    def validate_params(self, raw: Any) -> dict[str, Any]:
        """把任意 JSON 收敛成该类型的规范参数 dict。

        Args:
            raw: 管理端提交的原始参数（可以是 None、缺字段或带未知字段）。

        Returns:
            补齐默认值、剔除未知字段后的参数 dict，可直接存库。

        Raises:
            pydantic.ValidationError: 参数类型或取值范围不符合 schema。
        """

        if raw is None:
            raw = {}
        return self.params_model.model_validate(raw).model_dump()


TASK_TYPE_SPECS: dict[str, TaskTypeSpec] = {
    spec.task_type: spec
    for spec in (
        TaskTypeSpec(
            task_type="freshrss_sync",
            description="FreshRSS 增量同步：把 FreshRSS 里的新新闻拉取入库到 PostgreSQL（不向量化）。",
            params_model=FreshRssSyncTaskParams,
        ),
        TaskTypeSpec(
            task_type="index_pending",
            description="向量索引：把 PostgreSQL 里待索引的文档切块、向量化并写入 Qdrant。",
            params_model=IndexPendingTaskParams,
        ),
    )
}


def get_task_type_spec(task_type: str) -> TaskTypeSpec | None:
    """按类型名取注册项；不存在的类型返回 None。"""

    return TASK_TYPE_SPECS.get(task_type)


__all__ = [
    "FreshRssSyncTaskParams",
    "IndexPendingTaskParams",
    "TASK_TYPE_SPECS",
    "TaskTypeSpec",
    "get_task_type_spec",
]
