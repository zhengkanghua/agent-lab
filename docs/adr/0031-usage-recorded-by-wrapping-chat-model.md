---
status: accepted
---

# 用量采集点是包装后的模型客户端

记录一次模型调用的 token 用量，采集点放在 `build_chat_model` 这个全项目唯一的 provider 分叉点外面包一层，包装实例在构造时钉死自己的 `call_kind`（主模型 / 备用模型 / 摘要压缩），因此所有模型调用必然经过采集，且来源不需要从调用现场推断。流式事件和 LangChain 回调两条路都被实测排除。

## Considered Options

**沿用流式 `messages` 事件里的用量累加。** 那是改动最小的做法，但框架的 `InternalCallTransformer` 会主动把中间件内部的调用从流里删掉，所以历史摘要压缩的调用一次都记不到——长会话里最烧钱的那部分正好落在盲区。

**用 LangChain 的 `on_llm_end` 回调。** 实测能捕获全部调用（含摘要压缩），但回调拿到的 `metadata` 恒为空、自定义 `tags` 也传不下来，于是分不清某次调用来自主模型、降级还是摘要压缩。账目能看见总额，却看不出钱花在哪一类调用上。

## Consequences

包装类必须让 `bind_tools` / `bind` 返回包装自身。委托后返回内层 runnable 会让用量统计全部静默失效——不报错、只是没有记录，是最难发现的那种坏法。`test_agent_chat_model.py` 逐项断言模型的可观测属性（`model_name`、`default_headers`、`request_timeout` 等），包装类要显式委托这些属性，并且**不要**用 `__getattr__` 兜底转发下划线开头的方法，否则会把采集钩子 `_generate` / `_agenerate` 一起转发出去。

来源标签是**实例属性**，所以摘要压缩不能复用主模型的包装实例。现在的装配里 `summarization_model=primary_model` 传的是同一个对象，直接照搬会让摘要调用被记成主模型调用——它仍然是同一个底层客户端，但要包成另一个带 `summarization` 标签的实例。这是本决策唯一一处必须跟着改的装配。

模型层拿不到 `ModelRequest`，采集器只能靠 `langgraph.runtime.get_runtime()` 读当前运行的上下文才知道这是哪个账号、哪一次运行。已实测摘要压缩路径同样拿得到——摘要调用发生在中间件内部、不在 model 节点，这是它需要单独验证的原因。

`ChatOpenAI` 在自建 `base_url` 下 `stream_usage` 默认为 `False`，于是请求不带 `stream_options={"include_usage": true}`。而 Agent 的**生产入口本来就是流式调用**：`stream_agent_events` 用 `graph.astream(stream_mode=["updates", "messages"])`，`messages` 这个 mode 会挂上 langgraph 的 `StreamMessagesHandler`，`langchain_core` 的 `_should_stream` 据此判定走流式 HTTP。按 OpenAI 官方的流式契约，上游此时不会回 `usage_metadata`，用量就静默记成 0。本项目的生产上游（中转站）实测无视这个约定、流式响应里也会回用量，因此当时恰好有数——但那是上游宽容，不是我们能保证的东西。所以 `build_chat_model` 显式打开 `stream_usage=True`，把「请求用量」变成本项目的主动契约。

这一段曾经写成「当前 Agent 走非流式调用所以用量正常，一旦将来改成流式才会静默变空」。那个说法与实现相反，而且正是它让 `stream_usage` 这件事被当成未来风险而漏掉了一次；改流式调用前先读 `agent/chat_model.py` 里 `stream_usage` 那段。
