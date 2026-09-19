---
status: accepted
---

# 流式路由不用请求级数据库 Session

返回 `StreamingResponse` 的路由（现在只有 `POST /agent/chat`）不许用 `Depends(get_db_session)` 那种
请求级 Session。需要查库的话，注入一个持有 **session 工厂** 的 Service，由它每次操作自己开一个
Session、提交、立刻关（`get_agent_thread_service` 就是这么做的）。

**原因是 FastAPI 的依赖清理时机。** 请求级 Session 要等响应彻底结束才归还连接，而流式响应的「结束」
是流关闭之后——一次长对话可能几分钟。那意味着一条业务连接被一次对话占满全程，几个人同时聊就能把连接
池占空，而故障表现和 Agent 毫无关系：**检索页开始报数据库不可用**，因为它抢不到连接。排查的人会去看
检索链路，方向从一开始就是错的。

## Considered Options

**照常用请求级 Session，把连接池调大。** 改一个数字就完事。但池大小是常量、并发数不是，调大只是把
悬崖往后挪；而且那些连接在几分钟里绝大部分时间是空闲的，纯属浪费。真正的问题是「占用时长和请求时长
挂钩」，不是「池子太小」。

**在生成器里面查库，用短 Session。** 连接占用问题解决了。但流一旦开始、响应头就已经发出去了，之后的
失败只能是一个 SSE `error` 事件，不能是 HTTP 状态码。归属校验失败本该是 404——一个明确的「这个会话
不是你的」——变成 200 加一个事件，前端得为同一件事写两条完全不同的处理路径，代理和缓存的行为也跟着
变。校验必须在流开始之前，那就不能在生成器里面。

**路由里照常拿 Session，但手工在开始流之前 close 掉。** 能 work，而且不用新抽象。但这个纪律是隐形
的：没有任何东西会提醒下一个人「这条路由的 Session 必须提前关」，忘了也不报错，只在并发上来以后表现
成别的页面故障。让 Service 持有工厂，正确做法就成了默认行为，忘不掉。

## Consequences

**工厂必须由构造参数传进去**，不能在 Service 内部 import `db.session.async_session_factory`——那是
import 时就绑好真实 `DATABASE_URL` 的模块级对象，直接引用会让离线测试没有注入点，测试会真去连
PostgreSQL。所以 `tests/app_helpers.py` 默认把这个依赖覆盖成内存替身，漏写覆盖的后果是「用了替身」
而不是「连了真库」。那个文件开头记着同类的坑：`agent_runtime_factory` 曾在 5 个测试文件里被集体漏掉，
每次进 lifespan 都要等满 psycopg 连接超时，而 lifespan 里的 `except Exception` 把失败咽掉了，所以很长
时间没人发现。

**一次对话请求多一次独立的数据库往返**：先 `ensure_thread` 提交，再开始流。几十毫秒，换来的是连接
占用与对话时长解耦。

**`last_active_at` 只在每轮开始时写一次，流结束后不再写。** 所以一场聊了半小时的对话，它记的是最后
那轮提问的时刻，不是最后一个 token 的时刻。列表排序用它足够了；流结束后再写一次意味着要么在生成器里
再开一个 Session（回到被否掉的方案），要么在流结束的 `finally` 里做，而那时客户端可能已经断开。

以后再加流式路由（比如流式的检索或摘要），同一条规则照用。这条约束在 `api/dependencies.py` 和
`services/agent_thread_service.py` 的 docstring 里都指向本 ADR。
