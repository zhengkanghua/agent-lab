"""独立 scheduler 中的文档待办消费者；不依赖 cron 配置或 API 进程内存。"""

import asyncio
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


class DocumentProcessingConsumer:
    def __init__(self, batch_factory, *, poll_seconds=5, shutdown_grace_seconds=10):
        self._factory = batch_factory
        self._poll_seconds = poll_seconds
        self._grace = shutdown_grace_seconds
        self._stopping = asyncio.Event()
        self._task = None

    async def start(self):
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="document-processing")

    async def _run(self):
        last_error = None
        while not self._stopping.is_set():
            try:
                result = await self._factory().run(batch_size=20, stale_after=timedelta(minutes=60))
                last_error = None
                if result.candidate_count or result.cleaned_count:
                    logger.info("文档批次完成 parsed=%d adopted=%d review=%d failed=%d cleaned=%d",
                                result.parsed_count, result.indexed_count, result.review_count,
                                result.failed_count, result.cleaned_count)
            except Exception as exc:
                # 配置缺失或持久写占用需要核实期间，保留待办并避免每次轮询刷相同日志。
                error = (type(exc).__name__, getattr(exc, "code", None))
                if error != last_error:
                    logger.error("文档消费暂停 error_type=%s code=%s", *error)
                last_error = error
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def close(self):
        """先让当前步骤收尾，超出宽限再取消；远端写入取消会保留未决占用。"""
        self._stopping.set()
        if self._task is None:
            return
        _, pending = await asyncio.wait({self._task}, timeout=self._grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
