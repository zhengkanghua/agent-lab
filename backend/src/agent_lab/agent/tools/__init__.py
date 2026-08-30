"""组装 Agent 的全部工具，是本包唯一对外出口。

上层（``agent.runtime``）只调用 ``build_agent_tools``，拿到一个 ``list[BaseTool]``，不需要
知道有几个工具、各自依赖什么。加工具时只改本文件和新工具模块，Runtime、中间件、路由都不动。

本包内所有工具都是只读的，见 ``docs/adr/0003-agent-v1-is-read-only.md``。本模块只组装对象，
不执行检索、不建立数据库连接。

**本包内 ``@tool`` 装饰的函数，docstring 是写给模型的**：LangChain 把整份 docstring 原样
当作工具描述送进模型上下文，所以那里只写模型需要判断「这个工具是什么、什么时候用」的内容，
连「什么时候该用它」也写在这里而不是系统提示词里——工具的用法跟着工具走，加工具才不用回头
改提示词。Args/Returns/Raises 和「异常交给中间件」这类维护者信息改用 ``#`` 写在函数体开头，
``Raises:`` 尤其不能留在 docstring 里：异常类名会随工具描述进模型上下文，绕过
``sanitize_tool_error`` 的脱敏。

这条只限被装饰的那个内层函数。模块 docstring、参数类 docstring 和外层 ``build_*_tool``
的 docstring 都不进模型上下文（参数类的类 docstring 实测不出现在 payload 里），照平时写。
"""

from langchain_core.tools import BaseTool

from agent_lab.agent.tools.read_document import SessionFactory, build_read_document_tool
from agent_lab.agent.tools.search_news import build_search_news_tool
from agent_lab.services.vector_search_service import VectorSearchService


def build_agent_tools(
    *,
    search_service: VectorSearchService,
    session_factory: SessionFactory,
) -> list[BaseTool]:
    """按固定顺序组装 Agent 可用的只读工具。

    顺序有意义但不影响正确性：工具的 schema 按这个顺序进入模型上下文，先出现的更容易被
    选中。``search_news`` 排在前面，因为正常链路总是「先检索、必要时再读全文」。

    Args:
        search_service: 进程级只读检索 Service。
        session_factory: 产出短命 ``AsyncSession`` 的工厂，供全文读取工具按调用开关。

    Returns:
        ``[search_news, read_document]``，可直接交给 ``create_agent(tools=...)``。

    Notes:
        只组装对象，不执行任何 I/O。两个工具都不具备写能力。
    """

    return [
        build_search_news_tool(search_service),
        build_read_document_tool(session_factory),
    ]


__all__ = ["build_agent_tools"]
