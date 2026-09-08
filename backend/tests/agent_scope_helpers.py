"""Agent 范围与真实 ToolRuntime 的离线夹具，不接入任何外部服务。"""

from langchain.tools import ToolRuntime

from agent_lab.agent.context import AgentContext
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.knowledge.scope import KnowledgeBaseSummary, ResolvedKnowledgeBaseScope

NEWS_SCOPE = ResolvedKnowledgeBaseScope(mode="selected", knowledge_bases=(
    KnowledgeBaseSummary(id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID, key="news", name="新闻", description="新闻资料"),
))


async def invoke_tool_message(tool, arguments, *, context=None):
    runtime = ToolRuntime(
        state={"messages": []}, context=context or AgentContext(scope=NEWS_SCOPE),
        config={}, stream_writer=lambda _: None, tool_call_id="call-test", store=None,
    )
    return await tool.ainvoke({
        "type": "tool_call", "id": "call-test", "name": tool.name,
        "args": {**arguments, "runtime": runtime},
    })


async def invoke_tool(tool, arguments, *, context=None):
    return (await invoke_tool_message(tool, arguments, context=context)).content
