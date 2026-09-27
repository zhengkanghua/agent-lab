"""采集器的实现：空实现（缺省值）与排队实现（生产用）。

排队实现把每次调用受理成一条队列记录，由后台任务按刷写间隔批量落库。这样做的理由集中在
``docs/adr/0033-usage-module-independent-of-domain.md``：写入不能挡在对话链路上，而每次模型调用
都单独开一次数据库往返会让用量库的抖动直接变成对话的抖动。

两个约束决定了它的形状：

* 采集入口 ``record`` 是**同步**的（取消路径上也要能调用它），所以它只做 ``put_nowait``；
* 采集失败绝不能变成对话失败，所以投递、刷写、排空的每个失败分支都各自兜住异常、只留日志。
"""

import asyncio
import contextlib
import logging
from typing import Any

from agent_lab.usage.contracts import UsageRecord
from agent_lab.usage.repository import UsageRepository


logger = logging.getLogger(__name__)

USAGE_QUEUE_CAPACITY = 10_000
"""队列容量。按「用量库挂多久不丢数据」定，而不是按突发倍数定：它是内存兜底，不是限流。"""

USAGE_FLUSH_BATCH_SIZE = 100
"""一次刷写最多写出多少条。"""

USAGE_FLUSH_INTERVAL_SECONDS = 1.0
"""刷写间隔。也是查询侧「约一秒延迟」的来源，以及关停排空的时间上限。"""

USAGE_COMMIT_RETRIES = 1
"""整批提交失败后的重试次数；仍失败就丢弃这一批，不重新入队。"""


class NoopUsageCollector:
    """什么都不记的采集器。

    它与「采集器缺席」在行为上等价，但把缺省值做成一个对象而不是 ``None``，可以省掉包装层
    里每一处 ``if collector is not None`` 的分支——那种分支漏一处就会让整条链路在缺省配置下
    静默不记账。
    """

    def record(self, record: UsageRecord) -> None:
        """丢弃这条记录。

        Args:
            record: 本次调用的事实；这里不读取它的任何字段，因此也不会因为它内容不全而失败。
        """

        return None

    async def drain(self) -> None:
        """空实现：没有待写的东西。"""

        return None


class QueuedUsageCollector:
    """进程内队列加批量落库的采集器，生产装配用的就是它。

    采集入口立即返回、从不向调用方抛异常；后台按刷写间隔把队列里最多一批记录写出去。队列有界，
    满了丢弃新记录并记日志；写入持续失败也记日志。**不做累计计数与账目守恒**——本期只要求
    「不把故障传回对话」和「丢了能在日志里看见」。

    Attributes:
        _queue: 有界队列。它只是内存兜底，不是限流：正常与异常情况下都不该被填满。
        _flusher: 后台刷写任务。首次受理记录时惰性启动；它存在与否不影响 ``record``。
        _dropped_in_outage: 本次故障（连续失败期间）已丢弃的条数，写成功一次就归零。日志里报的
            是这个数，因为「这一次丢了多少条」只有在故障有始有终时才有意义。
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        loop: asyncio.AbstractEventLoop,
        queue_capacity: int = USAGE_QUEUE_CAPACITY,
        batch_size: int = USAGE_FLUSH_BATCH_SIZE,
        flush_interval: float = USAGE_FLUSH_INTERVAL_SECONDS,
        commit_retries: int = USAGE_COMMIT_RETRIES,
        drain_timeout: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._loop = loop
        self._queue: asyncio.Queue[UsageRecord] = asyncio.Queue(maxsize=queue_capacity)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._commit_retries = commit_retries
        # 关停排空的时间上限。默认取一次刷写间隔（"不阻塞关停超过一次刷写间隔"）；给它一个
        # 单独的入口是为了让测试能把刷写间隔调得很小（用例要快）而排空仍有足够的时限。
        self._drain_timeout = flush_interval if drain_timeout is None else drain_timeout
        self._flusher: asyncio.Task[None] | None = None
        self._dropped_in_outage = 0
        self._in_flight = 0

    def record(self, record: UsageRecord) -> None:
        """受理一条记录：放进队列就返回，不等待、不抛异常。

        Notes:
            队列满意味着用量库已经长时间写不进去（容量按「能扛多久」定），此时丢弃这条新记录并
            记一条日志。丢弃而不是阻塞：挡在对话链路上等一个坏掉的数据库，等于把次要库的故障
            升级成主要功能不可用。
        """

        try:
            self._ensure_flusher()
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped_in_outage += 1
            logger.warning(
                "用量队列已满，本次故障累计丢弃 %d 条记录（用量库可能长时间不可用）",
                self._dropped_in_outage,
            )
        except Exception:
            logger.exception("用量记录投递失败，已忽略以免影响对话")

    async def drain(self) -> None:
        """关停前尽力把队列刷完；刷不完就留日志，不让关停无限期挂住。

        Notes:
            上限取一次刷写间隔：关停不能被一个坏掉的数据库拖住。超时或刷写中断时，剩下多少条
            都写在日志里——静默丢掉最后几条正是本项目参考的开源网关踩过的坑。
        """

        flusher, self._flusher = self._flusher, None
        if flusher is not None:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
        try:
            await asyncio.wait_for(self._flush_until_empty(), timeout=self._drain_timeout)
        except TimeoutError:
            logger.warning(
                "用量队列关停排空超时，仍有 %d 条记录未写入",
                self._remaining(),
            )

    def _ensure_flusher(self) -> None:
        """首次受理记录时启动后台刷写任务；任务已经结束（异常）就重启一个。"""

        if self._flusher is None or self._flusher.done():
            self._flusher = self._loop.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        """按间隔刷写。

        队列为空时什么都不做、一次数据库访问都不发生——这一点由 ``_flush_once`` 的
        「取不到就返回」保证，此处不再重复判空：两个地方各判一次，早晚会有一处被改动而另一处
        没跟上。
        """

        while True:
            await asyncio.sleep(self._flush_interval)
            with contextlib.suppress(Exception):
                # 单次刷写的失败已经在里面记过日志；这里兜住是为了让循环活下去——刷写任务
                # 死掉意味着之后所有记录都只会堆在队列里，直到溢出才开始丢。
                await self._flush_once()

    async def _flush_until_empty(self) -> None:
        """连续刷写到队列空为止；某一次刷写失败就停手，把剩余的条数留给调用方记日志。"""

        while not self._queue.empty():
            if not await self._flush_once():
                logger.warning(
                    "用量队列关停排空中断，仍有 %d 条记录未写入",
                    self._remaining(),
                )
                return

    def _remaining(self) -> int:
        """还没落库的条数：队列里排队的，加已取出但还没写成功的那一批。

        少了后一半，关停日志会报 0 条——而那正是最不能静默丢掉的那部分（写一半被取消的）。
        """

        return self._queue.qsize() + self._in_flight

    async def _flush_once(self) -> bool:
        """写出一批记录。

        Returns:
            这一批是否写成功（或队列本来就是空的）。失败时这一批**丢弃、不重新入队**：重新入队
            在数据库卡死时会让内存持续增长，把「数据库慢」放大成「进程 OOM」。

        Notes:
            先在本进程里取出最多一批，再一次事务提交。提交失败按 ``commit_retries`` 重试一次，
            仍失败则丢弃该批并记一条日志。

            ``_in_flight`` 只在「这一批已有结论」（写成功，或重试耗尽后丢弃）时归零；被取消时
            它保持不零，好让关停日志如实报出还有多少条没写。
        """

        batch: list[UsageRecord] = []
        while len(batch) < self._batch_size and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if not batch:
            return True

        self._in_flight = len(batch)
        for attempt in range(self._commit_retries + 1):
            try:
                async with self._session_factory() as session:
                    repository = UsageRepository(session)
                    for record in batch:
                        await repository.add(record)
                    await session.commit()
            except Exception:
                if attempt < self._commit_retries:
                    continue
                logger.warning(
                    "用量库写入失败，本次丢弃 %d 条记录（重试 %d 次后仍失败）",
                    len(batch),
                    self._commit_retries,
                )
                self._in_flight = 0
                return False
            # 写成功一次就把「本次故障」的计数归零：它衡量的是一段连续的失败。
            self._in_flight = 0
            self._dropped_in_outage = 0
            return True
        return False


__all__ = [
    "USAGE_COMMIT_RETRIES",
    "USAGE_FLUSH_BATCH_SIZE",
    "USAGE_FLUSH_INTERVAL_SECONDS",
    "USAGE_QUEUE_CAPACITY",
    "NoopUsageCollector",
    "QueuedUsageCollector",
]
