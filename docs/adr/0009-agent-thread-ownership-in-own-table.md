# Agent 会话的归属放自己的表

「某个会话属于谁」记在一张由 Alembic 管理的业务表 `agent_threads` 里（`thread_id` 主键、`user_id`
外键指向 `users`、`title`、`created_at`、`last_active_at`），与 checkpointer 的四张表完全分开。归属判断
只有一处实现：`AgentThreadService` 里带 `WHERE user_id` 的那几条语句。会话列表也从这张表读。

原来没有这张表，`POST /agent/chat` 是 `thread_id = chat_request.thread_id or uuid4()`：前端给什么 id
就用什么 id，而 checkpointer 只按 id 取历史、不问是谁。今天靠 `Depends(current_superuser)` 兜着，
超级用户之间互相能读，超级用户以外进不来；但放宽权限本来就在计划里（`api/agent_chat.py` 里那条注释写着），
一旦放宽，猜到或抄到一个 UUID 就能读别人的完整对话，也能接着别人的会话继续聊。

## Considered Options

**把归属写进 checkpointer 的 metadata。** LangGraph 允许往 checkpoint 里塞自定义元数据，不用建表。
但要读到它得先加载一个 checkpoint，「列出我的会话」就变成「把所有会话的历史都读出来再过滤」；`last_active_at`
排序和分页更是没法做。更关键的是它把一个安全字段放进了第三方拥有的 schema 里——那四张表的结构归
`langgraph-checkpoint-postgres` 管，ADR 0004 已经决定不在那里放我们的知识。也没法建指向 `users` 的外键，
删账号会留下永久孤儿。

**把账号编进 thread_id，比如 `<user_id>:<uuid>`，不建表。** 零存储成本。但这是「靠解析字符串做鉴权」：
id 由前端传上来，伪造一个别人的前缀不需要任何成本，而校验时除了这个字符串本身没有第二个信息源可比对——
等于没有校验。顺带还把账号 id 暴露在 URL 和模型的 config 里。

**在 `users` 表上加一列存会话 id 数组。** 不用新表。但一个账号的会话会长到几百个，全塞进一行会让这一行
反复重写、并发提问时互相锁；而且数组里没法给 `last_active_at` 建索引，列表排序只能取回全部再在内存里排。

**不做归属，继续靠「只有超级用户能用 Agent」挡着。** 这是现状，成本为零。但它把一个访问控制问题
寄存在一个将来一定会变的权限设置上，而且变更点（放宽权限）和事故点（跨账号读对话）离得很远，改的人不会
想到这里。老板明确要求把这个洞补上。

## Consequences

新增一张表和一个迁移。**回滚这个迁移会让每个会话变成孤儿**——历史还在 checkpointer 里，但没有归属记录，
网页上既列不出来也删不掉。迁移的 `downgrade()` 里写了这句警告。

归属记录在**流开始之前**就写好，所以首轮运行失败会留下一个「有会话、没消息」的行。这不是脏数据，
是如实记录：用户确实开了这个会话。回放接口对它返回空轮次列表，前端显示成一个空会话，可以接着聊。

归属功能上线**之前**产生的历史没有归属记录，无法追认（当时没记是谁聊的）。它们成为孤儿，由
`agent-lab prune-orphan-threads` 清理，默认只预演、加 `--yes` 才真删。删账号时业务表这一行会级联删除，
但 checkpointer 里的历史不在外键图里，同样靠这个命令回收。

删除会话要动两个存储（checkpointer 的历史、业务表的归属记录），跨两个连接池不可能一个事务，所以顺序
是选出来的：先清历史、后删归属记录。中途失败留下「历史没了、归属还在」，用户再点一次删除就干净了；
反过来会留下查不到也删不掉的历史。这个顺序写在 `api/agent_threads.py` 的 docstring 里，不要交换。

归属条件在真库上到底生效没有，编译 SQL 文本证明不了。`tests/test_agent_thread_service.py` 只能挡住
「忘了写条件」，挡不住「写了但不生效」（类型不匹配、链式 `where` 覆盖、迁移列名错）。所以另有
`tests/test_agent_thread_ownership_integration.py` 跑真库、默认跳过，用
`RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1` 启用。这两层都要在。
