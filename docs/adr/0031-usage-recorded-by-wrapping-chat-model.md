---
status: accepted
---

# 用量采集点是包装后的模型客户端

记录一次模型调用的 token 用量，采集点把 `build_chat_model` 产出的客户端包一层：包装发生在 Agent 运行时装配处（`agent/runtime.py`），不放进 `build_chat_model` 内部——注入假模型的路径也必须被包住，否则端到端测试一条记录也不会有。所有模型调用因此必然经过采集，包括中间件内部发起的历史摘要压缩。流式事件里的用量累加被实测排除。

## Considered Options

**沿用流式 `messages` 事件里的用量累加。** 那是改动最小的做法，但那行累加只认模型节点的产出，而历史摘要压缩的调用发生在中间件内部、不在模型节点上，于是一次都记不到——长会话里最烧钱的那部分正好落在盲区。**成因不在框架一侧**：实测摘要调用产生的增量确实出现在消息流里（节点名就是那个中间件），把它挡在合计之外的是自己那行过滤；触发摘要压缩的一次运行里，账本有两条记录（摘要 + 回答），按模型节点累加只得到一条。

## Consequences

包装类必须让 `bind_tools` / `bind` 的结果仍然经过包装：拿底下产出的**绑定结果**重新挂到包装实例上（`agent/usage_recording.py` 的 `_with_runner`）。两个方向都会静默失效——返回内层 runnable 会丢掉记账（不报错、只是没有记录），在绑定方法里直接返回包装自身会丢掉工具（模型不再调用检索工具，同样不报错）。

模型的可观测属性必须逐项手工委托（`model_name`、`default_headers`、`request_timeout`、`profile`、`_get_ls_params()` 等），并且**不要**用 `__getattr__` 兜底转发下划线开头的方法，否则会把采集钩子 `_generate` / `_agenerate` 一起转发出去。守住这一点的是 `test_agent_usage_recording.py`：它把真实客户端包起来后逐项断言属性与未包装时一致——`test_agent_chat_model.py` 只覆盖构造入口那一段，根本不经过包装。

**本期不记录来源标签**（主模型 / 备用模型 / 摘要压缩）：账本回答「花了多少」，不回答「花在哪一类调用上」，所以摘要压缩照旧复用主模型那个包装实例，装配保持现状。不过「标签是实例属性」这件事仍然成立——将来要区分来源时，摘要压缩必须另包一个带标签的实例，直接复用会让摘要调用被记成主模型调用。

模型层拿不到 `ModelRequest`，采集器只能靠 `langgraph.runtime.get_runtime()` 读当前运行的上下文才知道这是哪个账号、哪一次运行。已实测摘要压缩路径同样拿得到——摘要调用发生在中间件内部、不在 model 节点，这是它需要单独验证的原因。

`ChatOpenAI` 在自建 `base_url` 下 `stream_usage` 默认为 `False`，于是请求不带 `stream_options={"include_usage": true}`。而 Agent 的**生产入口本来就是流式调用**：`stream_agent_events` 用 `graph.astream(stream_mode=["updates", "messages"])`，`messages` 这个 mode 会挂上 langgraph 的 `StreamMessagesHandler`，`langchain_core` 的 `_should_stream` 据此判定走流式 HTTP。按 OpenAI 官方的流式契约，上游此时不会回 `usage_metadata`，用量就静默记成 0。本项目的生产上游（中转站）实测宽容、流式响应里也会回用量，但那是上游行为、不是能依赖的保证。所以 `build_chat_model` 显式打开 `stream_usage=True`，把「请求用量」变成本项目的主动契约；上线后跑一次真实会话，6 次调用全部记为上游自报、token 非零，这个开关确实在起作用。改动流式调用前先读 `agent/chat_model.py` 里 `stream_usage` 那段。

**每次调用恰好一条记录。** 正常返回记一条完成；抛出异常（含被取消）记一条失败后原样再抛。失败的记录 token 记 0、来源记缺失，但账号、会话与运行照取——失败的那次调用同样属于某次提问。吞掉异常会把「已停止」变成「继续跑」，不记失败则会让「停止」在账本上看不见。
