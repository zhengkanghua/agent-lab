"""承载生成式 Agent：让模型带着新闻检索结果回答提问。

本包位于 Service 层之上、API 层之下。它组装模型客户端、只读工具、中间件和会话记忆，
并把 LangGraph 的事件流翻译成可直接发给浏览器的 SSE 帧。

第一版只读：包内任何工具都不写 PostgreSQL 业务表、不写 Qdrant、不改 processing_status，
见 ``docs/adr/0003-agent-v1-is-read-only.md``。本包不实现自动调度和后台任务，也不承担
检索本身——检索仍由 ``services.vector_search_service`` 负责，工具只是它的薄封装。
"""
