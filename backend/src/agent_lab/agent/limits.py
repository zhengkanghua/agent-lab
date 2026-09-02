"""定义 Agent 运行的有界执行参数。

本模块位于 Agent 层的共享配置层，只保存不会访问环境或外部服务的常量。它不解析 HTTP
输入、不调用模型或工具，也不表达部署差异——这些是安全上限，不是可按环境调节的旋钮，
所以刻意不放进 ``.env``：改它们等于改「一次对话最坏情况能消耗多少」，属于代码决策。

和 ``pipeline.limits`` 的区别：那份约束的是 CLI 与手动 HTTP 写流水线，本份约束的是
Agent 的一次运行（run）。两者的调用方和失败后果都不同，共用一个文件会让边界含糊。
"""

# ---- 循环上限：防止模型自己和自己聊到没完，也防止一次提问烧掉大量额度 ----

# 一次运行内最多几次模型调用。达到后 ModelCallLimitMiddleware 直接结束运行并返回
# 已有内容（exit_behavior="end"），不再继续调工具。8 次足够「检索一次、必要时再补一次、
# 然后作答」这类正常链路。
MODEL_CALL_RUN_LIMIT = 8

# 一次运行内最多几次工具调用。达到后 ToolCallLimitMiddleware 让模型继续（
# exit_behavior="continue"），只是不再允许调工具，所以模型仍能用已有材料作答。
TOOL_CALL_RUN_LIMIT = 12

# 模型调用与工具调用各自的重试次数（不含首次）。配合中间件顺序才有效，见
# docs/adr/0005-middleware-order-semantics.md。
MODEL_RETRY_MAX = 2
TOOL_RETRY_MAX = 2

# 重试的首次退避秒数。指数退避的基数由中间件默认值（backoff_factor=2）决定。
RETRY_INITIAL_DELAY_SECONDS = 1.0

# 触发历史摘要压缩的消息条数阈值，以及压缩后保留的最近消息条数。
# 只按条数触发（不按 token），因为按 token 触发需要可靠的 token 计数器，而中转站的
# 计费模型和分词器我们并不掌握，条数是此处唯一能确定的量。
SUMMARIZATION_TRIGGER_MESSAGES = 40
SUMMARIZATION_KEEP_MESSAGES = 20


# ---- 输入上限：约束用户和外部内容能往模型上下文里塞多少 ----

# 自定义系统提示词的字符上限。超过直接拒绝请求，不截断——截断会把提示词砍成半句，
# 模型的行为反而更难预期。
MAX_SYSTEM_PROMPT_CHARS = 4000

# 单条用户消息的字符上限。
MAX_USER_MESSAGE_CHARS = 4000


# ---- 工具输出上限：检索结果和正文都会进入模型上下文，必须有界 ----

# search_news 一次最多返回几篇新闻、每篇最多几个片段。刻意小于
# schemas.document_search 的 MAX_DOCUMENT_LIMIT（100）：那是给人看的分页上限，
# 这里是给模型看的上下文预算。
SEARCH_TOOL_MAX_DOCUMENTS = 5
SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT = 2

# search_news 的 within_days 上限。365 天不是「语料库最多存一年」，而是「再往回问就等于
# 不限时间」——超过一年的窗口对排序几乎没有影响，却让模型有机会填出 99999 这种它自己也
# 说不清的值。给个明确上限，模型填超了会被参数校验挡下并看到范围说明，比默默接受更好。
SEARCH_TOOL_MAX_WITHIN_DAYS = 365

# read_document 返回的正文字符上限。超过则截断并在末尾标注被截断——这里截断是对的，
# 因为正文是数据不是指令，缺尾部只是信息不全，不会让模型误解任务。
READ_DOCUMENT_MAX_CHARS = 6000


# ---- 启动自检 ----

# 启动时向上游拉模型列表的超时秒数。刻意远小于 LLM_REQUEST_TIMEOUT_SECONDS（默认 60）：
# 那个约束的是「模型思考多久」，这个约束的是「启动多等多久」。列一下有哪些模型是个极轻的
# 请求，5 秒拿不到就说明上游此刻不健康，那种情况下继续等只是延迟服务上线——校验拿不到
# 结果时是放过而不是拒绝，所以等下去也换不来别的结论。
MODEL_CATALOG_TIMEOUT_SECONDS = 5.0


# ---- 流式传输 ----

# SSE 心跳间隔秒数。作用是让反向代理和浏览器都确信连接还活着：模型「想」的时候可能
# 十几秒不产出任何 token，中间任何一跳的空闲超时都可能掐掉连接。
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0


__all__ = [
    "MAX_SYSTEM_PROMPT_CHARS",
    "MAX_USER_MESSAGE_CHARS",
    "MODEL_CALL_RUN_LIMIT",
    "MODEL_CATALOG_TIMEOUT_SECONDS",
    "MODEL_RETRY_MAX",
    "READ_DOCUMENT_MAX_CHARS",
    "RETRY_INITIAL_DELAY_SECONDS",
    "SEARCH_TOOL_MAX_DOCUMENTS",
    "SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT",
    "SEARCH_TOOL_MAX_WITHIN_DAYS",
    "SSE_HEARTBEAT_INTERVAL_SECONDS",
    "SUMMARIZATION_KEEP_MESSAGES",
    "SUMMARIZATION_TRIGGER_MESSAGES",
    "TOOL_CALL_RUN_LIMIT",
    "TOOL_RETRY_MAX",
]
