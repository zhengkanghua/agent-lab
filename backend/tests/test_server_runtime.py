"""生产事件循环需兼容 psycopg 的 Windows 异步连接。"""

import asyncio

from agent_lab.runtime import selector_loop_factory


def test_server_loop_factory_is_psycopg_compatible():
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
