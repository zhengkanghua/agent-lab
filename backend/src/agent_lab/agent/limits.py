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

# search_documents 一次最多返回几篇文档、每篇最多几个片段。刻意小于
# schemas.document_search 的 MAX_DOCUMENT_LIMIT（100）：那是给人看的分页上限，
# 这里是给模型看的上下文预算。
SEARCH_TOOL_MAX_DOCUMENTS = 5
SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT = 2

# search_documents 的 within_days 上限。365 天不是「语料库最多存一年」，而是「再往回问就等于
# 不限时间」——超过一年的窗口对排序几乎没有影响，却让模型有机会填出 99999 这种它自己也
# 说不清的值。给个明确上限，模型填超了会被参数校验挡下并看到范围说明，比默默接受更好。
SEARCH_TOOL_MAX_WITHIN_DAYS = 365

# read_document 返回的正文字符上限。超过则截断并在末尾标注被截断——这里截断是对的，
# 因为正文是数据不是指令，缺尾部只是信息不全，不会让模型误解任务。
READ_DOCUMENT_MAX_CHARS = 6000

# 一次工具调用的时长上限。**必须存在**：一次不返回的工具调用会让这次运行卡住，而这次运行
# 仍然算「活着」（最后活跃时刻照常续期，不会被判成僵尸），于是这个会话一直没法提交新提问——
# 只能等用户按停止或进程重启。初值与同类检索超时同量级。
#
# 超时后走现有的工具错误路径（见 ``api/error_contract.AGENT_TOOL_ERROR_RULES`` 的
# ``agent_tool_timeout``）：模型收到一句安全文案，自己决定重试还是直接作答，整次运行不失败。
# 注意重试中间件在内层，所以一次挂住的调用实际上会被重试 ``TOOL_RETRY_MAX`` 次才交回模型，
# 耗时是「超时 × (1 + 重试次数)」这个有限值，不会无限期挂住。
TOOL_CALL_TIMEOUT_SECONDS = 30.0


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


# ---- 脱离连接的运行（见 ADR 0035 与 0037）----

# 运行驱动者等待下一个事件时的时间片。它同时是「用户按下停止」到「运行开始收尾」这条
# 路径的上限之一：每等满一个时间片，驱动者就看一眼内存里的停止标志。
RUN_EVENT_POLL_INTERVAL_SECONDS = 1.0

# 运行驱动者往库里续 ``last_active_at`` 的节奏。续期是驱动者自己的循环在做，与「一次模型
# 调用跑了多久」无关——那条链路上有重试、降级和工具调用，任何按它推算的阈值都会算错。
RUN_LIVENESS_UPDATE_INTERVAL_SECONDS = 30.0

# 失活（僵尸）阈值：``last_active_at`` 超过这么久没有续期，就认为那次运行已经中断并释放
# 会话。**由上面的续期间隔决定，不由操作超时决定**，取四倍余量：偶尔晚一拍不会把还活着的
# 运行判成僵尸（那会让第二次运行进来写同一个会话），而进程崩溃后最多两分钟会话自动解锁。
RUN_ZOMBIE_THRESHOLD_SECONDS = 4 * RUN_LIVENESS_UPDATE_INTERVAL_SECONDS

# 删除一个正在运行的会话时，等它收尾的上限与轮询间隔。
#
# 「先暂停再删除」听起来像多余的一步，其实那个字就是让暂停**真的生效**：停止是协作式、跨进程的
# （收到 DELETE 的进程不一定跑着这次运行），所以删除只能先写停止请求，等运行自己释放占位。而运行
# 在收尾之前一直在往会话历史里写（不只是收尾那一次），不确认它停了就去清历史，它会在我们清空之后
# 继续写回来，留下一条查不到也删不掉的孤儿会话。
#
# 上限不能去掉：万一运行正好卡在一次不响应取消的调用里，无限等会让删除请求挂住，而「删除请求挂住
# 比留下一次未完成的运行更糟」（见 spec）。正常路径下停止在 1~2 秒内完成，10 秒是五倍余量。
DELETE_RUNNING_THREAD_WAIT_SECONDS = 10.0

# 等待期间查「占位释放了没有」的间隔。取小值只为让等待结束时尽快返回，不影响正确性。
DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS = 0.2

# 每个 API 进程批量读取「自己手上在跑的那些运行」状态的节奏（在途运行 id、停止请求时刻）。
# 批量读而不是逐个运行查一次，是为了让负载与「一个进程挂了多次运行」无关。
RUN_STATE_POLL_INTERVAL_SECONDS = 1.0


__all__ = [
    "DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS",
    "DELETE_RUNNING_THREAD_WAIT_SECONDS",
    "MAX_SYSTEM_PROMPT_CHARS",
    "MAX_USER_MESSAGE_CHARS",
    "MODEL_CALL_RUN_LIMIT",
    "MODEL_CATALOG_TIMEOUT_SECONDS",
    "MODEL_RETRY_MAX",
    "READ_DOCUMENT_MAX_CHARS",
    "RETRY_INITIAL_DELAY_SECONDS",
    "RUN_EVENT_POLL_INTERVAL_SECONDS",
    "RUN_LIVENESS_UPDATE_INTERVAL_SECONDS",
    "RUN_STATE_POLL_INTERVAL_SECONDS",
    "RUN_ZOMBIE_THRESHOLD_SECONDS",
    "SEARCH_TOOL_MAX_DOCUMENTS",
    "SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT",
    "SEARCH_TOOL_MAX_WITHIN_DAYS",
    "SSE_HEARTBEAT_INTERVAL_SECONDS",
    "SUMMARIZATION_KEEP_MESSAGES",
    "SUMMARIZATION_TRIGGER_MESSAGES",
    "TOOL_CALL_RUN_LIMIT",
    "TOOL_CALL_TIMEOUT_SECONDS",
    "TOOL_RETRY_MAX",
]
