"""应用服务器的跨平台运行时配置。

这个小模块只解决一个 Windows 兼容问题：Uvicorn 默认用 Proactor 事件循环，而
Psycopg 的异步连接要求 Selector 事件循环，所以提供一个工厂在启动时换上正确的
loop。main.py 启动 uvicorn 时通过 ``--loop news_vector_service.runtime:selector_loop_factory``
指定它。
"""

import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """创建与 Psycopg 异步连接兼容的事件循环。

    Uvicorn 在 Windows 单进程模式下默认使用 ProactorEventLoop，而 Psycopg 3
    的异步连接明确要求 SelectorEventLoop。Uvicorn 把自定义 ``module:function``
    直接作为无参 loop factory 调用，因此本函数必须返回事件循环实例。

    Returns:
        尚未运行的 ``SelectorEventLoop`` 实例，由 Uvicorn 的 Runner 负责运行和
        关闭。调用方不应在本函数内设置全局 event loop policy。
    """

    return asyncio.SelectorEventLoop()
