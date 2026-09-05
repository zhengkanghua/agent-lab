"""保护已经开始的异步收尾，重复取消不能跳过资源释放或终态保存。"""

import asyncio


async def finish_cleanup(coroutine):
    """调用者负责停止业务；这里只等待一次收尾，不重试业务或抢占占用。"""
    task = asyncio.create_task(coroutine)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
