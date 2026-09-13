"""切换时一次性把遗留文档待办接到公共任务；正常 Beat 不重建失败或取消的批次。"""

import asyncio
import sys


async def bootstrap():
    from agent_lab.db.session import async_session_factory, engine
    from agent_lab.task_assembly import bootstrap_legacy_document_processing
    try:
        async with async_session_factory() as session:
            run_id = await bootstrap_legacy_document_processing(session)
            print(f"document_processing_run_id={run_id}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(bootstrap())
