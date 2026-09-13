"""先在短事务取得投递资格，再发布 Redis；成功发布也仍会定期核对领取。"""

import logging

logger = logging.getLogger(__name__)


class TaskDispatcher:
    def __init__(self, store, publish, *, retry_seconds=5):
        self.store, self.publish, self.retry_seconds = store, publish, retry_seconds

    async def publish_due(self, *, run_id=None):
        try:
            messages = await self.store.reserve_deliveries(run_id=run_id, retry_seconds=self.retry_seconds)
        except Exception as error:
            logger.error("任务补投扫描失败 error_type=%s", type(error).__name__)
            return
        for identity, generation in messages:
            error_type = None
            try:
                await self.publish(str(identity), generation)
            except Exception as error:
                error_type = type(error).__name__
                logger.error("任务消息发布失败 run_id=%s error_type=%s", identity, error_type)
            try:
                await self.store.record_delivery(identity, generation, error_type)
            except Exception as error:
                logger.error("任务投递结果保存失败 run_id=%s error_type=%s", identity, type(error).__name__)
