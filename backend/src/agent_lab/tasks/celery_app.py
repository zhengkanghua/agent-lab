"""Celery 只负责消息和进程，不保存结果、不自行重试业务。"""

from uuid import UUID

from celery import Celery, signals

from agent_lab.config.redis import get_redis_settings
from agent_lab.config.task_queue import get_task_queue_settings
from agent_lab.tasks.process import close_process_runtime, get_process_runtime, reset_after_fork

settings = get_task_queue_settings()
redis_settings = get_redis_settings()
app = Celery("agent_lab", broker=redis_settings.url.get_secret_value())
app.conf.update(
    # 密码单独传给消息客户端，无需拼接 URL 或手工转义特殊字符。
    broker_password=redis_settings.password.get_secret_value() or None,
    task_default_queue=settings.name,
    task_serializer="json", accept_content=["json"],
    task_ignore_result=True, result_backend=None,
    task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=settings.publish_timeout_seconds,
    broker_transport_options={
        # 队列和未确认消息等辅助键一并隔离，避免与共享 Redis 的其他用途重名。
        "global_keyprefix": f"tasks:{settings.name}:",
        "visibility_timeout": settings.visibility_timeout,
        "socket_timeout": settings.publish_timeout_seconds,
        "socket_connect_timeout": settings.publish_timeout_seconds,
    },
    beat_scheduler="agent_lab.tasks.beat:PostgresScheduler",
    beat_max_loop_interval=settings.beat_poll_seconds,
    timezone="UTC", enable_utc=True,
)

signals.worker_process_init.connect(reset_after_fork, weak=False)
signals.worker_process_shutdown.connect(close_process_runtime, weak=False)
# solo 在 Worker 主进程执行；prefork 主进程未创建 Runtime 时这里自然无事可做。
signals.worker_shutdown.connect(close_process_runtime, weak=False)


@signals.beat_init.connect(weak=False)
def install_beat_shutdown(sender, **kwargs):
    """信号只请求停止；当前异步 tick 退出后，再由 Beat 主循环关闭资源。"""
    from celery.platforms import signals as process_signals

    # Celery 默认信号处理器立即 sync/close，可能重入正在运行的事件循环，
    # 随后的 Service.start finally 还会再关闭一次。让 Service 自己完成收尾。
    def stop_after_tick(*_args):
        sender.stop()

    process_signals.update(SIGTERM=stop_after_tick, SIGINT=stop_after_tick)


@app.task(name="agent_lab.execute", ignore_result=True)
def execute_task(run_id: str, generation: int):
    runtime = get_process_runtime()
    runtime.run(runtime.worker.execute(UUID(run_id), generation))
