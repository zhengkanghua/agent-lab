"""调度器独立进程入口。

此模块作为独立进程启动,只负责执行定时任务,不处理 HTTP 请求。
与 API worker 完全解耦,避免多 worker 环境下任务重复执行。

启动方式:
    docker compose up scheduler
    或本地测试: python -m agent_lab.scheduler_main
"""
import asyncio
import signal
import sys
import os

# Windows 控制台编码兼容：强制 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        # Python < 3.7 或其他情况下的降级处理
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

from agent_lab.config.scheduler import get_scheduler_settings
from agent_lab.db.session import async_session_factory, engine
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.repositories.scheduled_job_repository import ScheduledJobStore
from agent_lab.services.scheduler_runner import ScheduledJobRunner


def build_pipeline_write_runtime() -> PipelineWriteRuntime:
    """构建写入 Runtime（复制自 main.py）。"""
    from agent_lab.config.freshrss import get_freshrss_settings
    from agent_lab.config.ollama_embedding import get_ollama_embedding_settings
    from agent_lab.config.qdrant import get_qdrant_settings

    return PipelineWriteRuntime.build(
        session_factory=async_session_factory,
        freshrss_settings=get_freshrss_settings(),
        qdrant_settings=get_qdrant_settings(),
        ollama_settings=get_ollama_embedding_settings(),
    )


def build_scheduler_runner() -> ScheduledJobRunner:
    """构建调度器（复制自 main.py）。"""
    return ScheduledJobRunner(
        store_factory=lambda: ScheduledJobStore(async_session_factory),
        write_runtime_factory=build_pipeline_write_runtime,
        settings=get_scheduler_settings(),
    )


async def main():
    """调度器主函数。"""
    settings = get_scheduler_settings()

    # 检查调度器开关（可选,保留灵活性）
    if not settings.enabled:
        print("SCHEDULER_ENABLED=false,调度器不启动")
        print("如需启动调度器,请设置环境变量 SCHEDULER_ENABLED=true")
        return

    print("=" * 60)
    print("Agent Lab 调度器启动中...")
    print(f"时区: {settings.timezone}")
    print(f"宽限时间: {settings.misfire_grace_seconds}s")
    print("=" * 60)

    # 创建调度器
    scheduler = build_scheduler_runner()

    # 优雅关闭处理
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        print(f"\n收到信号 {sig},正在停止调度器...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 启动调度器
        await scheduler.start()
        print("✓ 调度器已启动")
        print("按 Ctrl+C 停止")

        # 阻塞在这里,保持进程运行
        await shutdown_event.wait()

    except Exception as e:
        print(f"✗ 调度器启动失败: {e}", file=sys.stderr)
        raise
    finally:
        print("正在停止调度器...")
        try:
            await scheduler.close()
        except Exception as e:
            print(f"调度器关闭时出错: {e}", file=sys.stderr)

        try:
            await engine.dispose()
        except Exception as e:
            print(f"数据库连接池关闭时出错: {e}", file=sys.stderr)

        print("✓ 调度器已停止")


if __name__ == "__main__":
    try:
        # Windows 兼容：使用 SelectorEventLoop 而不是 ProactorEventLoop
        # Psycopg 异步驱动要求 SelectorEventLoop
        if sys.platform == "win32":
            import selectors
            # 手动创建和设置事件循环，因为 asyncio.run() 会创建新的循环
            selector = selectors.SelectSelector()
            loop = asyncio.SelectorEventLoop(selector)
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(main())
            finally:
                loop.close()
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\n调度器已终止")
    except Exception as e:
        print(f"调度器异常退出: {e}", file=sys.stderr)
        sys.exit(1)
