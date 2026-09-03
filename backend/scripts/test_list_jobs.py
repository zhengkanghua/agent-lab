#!/usr/bin/env python3
"""临时脚本：列出数据库中的定时任务"""
import asyncio
import sys
from pathlib import Path

# 添加 src 到路径（脚本在 backend/scripts/ 下，src 在上两级）
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository
from agent_lab.db.session import async_session_factory


async def main():
    print("正在查询定时任务...")
    async with async_session_factory() as session:
        repo = ScheduledJobRepository(session)
        jobs = await repo.list_jobs()

        print(f"\n找到 {len(jobs)} 个定时任务:")
        for job in jobs:
            print(f"  - {job.key} (类型: {job.task_type})")
            print(f"    ID: {job.id}")
            print(f"    Cron: {job.cron_expr}")
            print(f"    启用: {job.enabled}")
            print(f"    参数: {job.params}")


if __name__ == "__main__":
    if sys.platform == "win32":
        import selectors
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()
    else:
        asyncio.run(main())
