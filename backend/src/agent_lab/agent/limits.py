"""定义 Agent 运行的有界执行参数。

本模块位于 Agent 层的共享配置层，只保存不会访问环境或外部服务的常量，以及由这些常量直接算出的
纯函数。它不解析 HTTP 输入、不调用模型或工具，也不表达部署差异——这些是安全上限，不是可按环境调节的旋钮，
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

# 历史摘要压缩的触发与保留口径：按**当轮模型的上下文窗口**占比算，触发取 80%、
# 压缩后保留 30%。窗口不是常量——它在每次模型调用时从本次运行的上下文里读，换一个窗口
# 不同的模型触发点跟着变，所以这里只固定比例，不固定任何窗口值。
SUMMARIZATION_TRIGGER_FRACTION = 0.8
SUMMARIZATION_KEEP_FRACTION = 0.3

# 摘要中间件构造期的占位窗口。**它的唯一职责是通过上游的构造期校验**：上游只要用到按比例
# 口径，就在 ``SummarizationMiddleware.__init__`` 末尾要求模型对象带 ``max_input_tokens``，
# 缺了直接 ``ValueError``、进程起不来。它不参与运行期的任何计算（运行期读的是当轮窗口），
# 所以不要拿它当真值去推任何结论。
SUMMARIZATION_PLACEHOLDER_WINDOW = 32768

# 压缩前的旧工具正文清理：每条正文只留头与尾，中间换成占位文字。三个值是一套口径，改一个就
# 改了清出多少量。头尾取这个量级是为了**真的清出量**：比「头 + 尾」短的原样保留，比它长的把
# 中段换掉，而工具正文动辄整篇文档，清一清能腾出很大一块。
#
# 占位文字会被用户看到（被清掉的轮次在界面上只剩它），所以它是对外文案，改动等于改用户看到
# 的东西。
PRUNED_TOOL_RESULT_HEAD_CHARS = 512
PRUNED_TOOL_RESULT_TAIL_CHARS = 256
PRUNED_TOOL_RESULT_PLACEHOLDER = "[... tool result middle pruned ...]"


# ---- 模型客户端缓存（见 ADR 0046）----

# 已经构造好的客户端被信任多久：这段时间内不再读目录表。它的代价是「改一条已有配置最多这么久
# 之后全副本生效」——这是换「不需要任何跨进程通知」的那一笔；新建条目没有这个延迟（未命中直接
# 查库）。取 60 秒是因为它同时要满足两头：管理员改完配置后在一分钟之内看到效果，而一个被频繁
# 使用的模型不会一分钟丢一次连接池与 TLS 连接（超时只重新校验那一行的内容，内容没变继续复用）。
MODEL_CLIENT_CACHE_TTL_SECONDS = 60.0

# 缓存里最多留几个模型客户端，超出后按最久未用先淘汰。每个条目就是一个客户端，也就带着一套
# 连接池与 TLS 会话，所以这个上界说的是「一个 API 进程最坏同时占多少上游连接池」。32 比一次
# 部署里真正被用过的模型数大一个量级（模型目录通常个位数到几十条），取这个量级是为了让淘汰在
# 正常情况下永不触发——它只兜住「目录被填进很多条目、而且每一条都被人选过」时内存不无界增长。
MODEL_CLIENT_CACHE_MAX_ENTRIES = 32


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

# 一次工具调用的时长上限。**必须存在**：一次不返回的工具调用会让这次运行卡住，而这次运行
# 仍然算「活着」（最后活跃时刻照常续期，不会被判成僵尸），于是这个会话一直没法提交新提问——
# 只能等用户按停止或进程重启。初值与同类检索超时同量级。
#
# 超时后走现有的工具错误路径（见 ``api/error_contract.AGENT_TOOL_ERROR_RULES`` 的
# ``agent_tool_timeout``）：模型收到一句安全文案，自己决定重试还是直接作答，整次运行不失败。
# 注意重试中间件在内层，所以一次挂住的调用实际上会被重试 ``TOOL_RETRY_MAX`` 次才交回模型，
# 耗时是「超时 × (1 + 重试次数)」这个有限值，不会无限期挂住。
TOOL_CALL_TIMEOUT_SECONDS = 30.0


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


# ---- 部署时的排空与接手（见 ADR 0040）----

# 进程收尾时等手上在途运行走到「可交接边界」的总预算。**是整次收尾的总预算，不是每个运行各一份**
# （收尾时对手上所有运行一起请求排空、并发等待，所以运行数不影响这个上限）。它覆盖的是典型的一次
# 模型调用或一批工具调用（几秒到三十秒），**不覆盖**最坏的重试链（模型 60 秒超时 × 3 次尝试再叠
# 主备回退、工具 30 秒 × 3）——那种 superstep 会被放弃。这是刻意的取舍：宁可最坏部署少等一会儿、
# 偶尔丢一次，也不让上限跟着「一次运行最坏能跑多久」往上顶。
#
# API 容器的停止宽限（180 秒）= uvicorn 关停超时（30 秒，写在 entrypoint.sh）＋ 本上限 ＋ 收尾写入
# 的余量，见 docs/container_deployment.md 与 docker-compose.yml。
RUN_DRAIN_TIMEOUT_SECONDS = 120.0

# 每个 API 进程扫描「被排空、等接手」标记的节奏。启动时先扫一次，之后按这个间隔再扫。
# 扫到就抢所有权接着跑（见 ADR 0040）。
RUN_HANDOVER_SCAN_INTERVAL_SECONDS = 30.0


__all__ = [
    "DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS",
    "DELETE_RUNNING_THREAD_WAIT_SECONDS",
    "MAX_SYSTEM_PROMPT_CHARS",
    "MAX_USER_MESSAGE_CHARS",
    "MODEL_CALL_RUN_LIMIT",
    "MODEL_CLIENT_CACHE_MAX_ENTRIES",
    "MODEL_CLIENT_CACHE_TTL_SECONDS",
    "MODEL_RETRY_MAX",
    "PRUNED_TOOL_RESULT_HEAD_CHARS",
    "PRUNED_TOOL_RESULT_PLACEHOLDER",
    "PRUNED_TOOL_RESULT_TAIL_CHARS",
    "RETRY_INITIAL_DELAY_SECONDS",
    "RUN_DRAIN_TIMEOUT_SECONDS",
    "RUN_EVENT_POLL_INTERVAL_SECONDS",
    "RUN_HANDOVER_SCAN_INTERVAL_SECONDS",
    "RUN_LIVENESS_UPDATE_INTERVAL_SECONDS",
    "RUN_STATE_POLL_INTERVAL_SECONDS",
    "RUN_ZOMBIE_THRESHOLD_SECONDS",
    "SEARCH_TOOL_MAX_DOCUMENTS",
    "SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT",
    "SEARCH_TOOL_MAX_WITHIN_DAYS",
    "SSE_HEARTBEAT_INTERVAL_SECONDS",
    "SUMMARIZATION_KEEP_FRACTION",
    "SUMMARIZATION_PLACEHOLDER_WINDOW",
    "SUMMARIZATION_TRIGGER_FRACTION",
    "TOOL_CALL_RUN_LIMIT",
    "TOOL_CALL_TIMEOUT_SECONDS",
    "TOOL_RETRY_MAX",
]
