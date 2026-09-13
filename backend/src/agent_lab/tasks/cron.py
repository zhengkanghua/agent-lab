"""只复用 CronTrigger 的五段式计算，校验、预览与 Beat 共用同一入口。"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger


class CronSchedule:
    def __init__(self, timezone="Asia/Shanghai", clock=None):
        self.timezone = timezone
        self._zone = ZoneInfo(timezone)
        self._clock = clock or (lambda: datetime.now(UTC))

    def parse_cron(self, expression):
        return CronTrigger.from_crontab(expression, timezone=self._zone)

    def next_after(self, expression, moment):
        result = self.parse_cron(expression).get_next_fire_time(None, moment + timedelta(microseconds=1))
        return result.astimezone(UTC) if result else None

    def upcoming_fire_times(self, expression, *, count=3):
        moments = []
        moment = self._clock()
        for _ in range(count):
            moment = self.next_after(expression, moment)
            if moment is None:
                break
            moments.append(moment)
        return moments, [moment.astimezone(self._zone).isoformat() for moment in moments]

    def planned_run_at(self, job):
        if not job.enabled:
            return None
        try:
            return self.next_after(job.cron_expr, self._clock())
        except ValueError:
            return None
