---
status: accepted
---

# 定时任务与后台处理抽为公共组件，用 Celery Beat/Worker 加 Redis

原先进程内的定时任务抽成项目内部公共组件，供知识库、Agent 及后续业务按同一契约接入；调度与执行改用 Celery Beat/Worker，Redis 做消息代理，PostgreSQL 保存受理、执行与恢复依据，替代 APScheduler 的进程内调度。必须换掉进程内调度，是因为它的执行状态只在进程内存里，服务重启就丢失已受理的执行；项目已经吃过一次同类教训——进程内互斥锁覆盖不了手动触发与不同进程，同一个业务写被并发执行。要的是「受理先落库、进程怎么挂都不丢」，这不是进程内调度器能提供的性质。选 Redis 是因为 Celery 官方支持这套接入、运维成本可控，不同时引入 RabbitMQ 或 RocketMQ。精确实现见 [后端架构说明](../../backend/docs/architecture.md) 的「公共任务组件」节。

## Considered Options

**启用 Celery result backend 保存执行结果。** PostgreSQL 已覆盖查询、结果与恢复，再存一份就是两个事实源，故障时会给出不一致的答案。

**把长 `ETA/countdown` 任务直接交给 Redis，靠 Celery 的 `autoretry` 计数。** 长延迟任务常驻 Redis 会占住内存，Celery 的计数与实际业务状态可能脱节；重试次数与下次尝试时间记在 PostgreSQL、由 Beat 到期投递，才能让「重试额度」和「业务当前状态」对齐。

## Consequences

不承诺「恰好一次」：PostgreSQL 与 Qdrant、原件存储之间没有共同事务，保证的是持久受理、可补投和受控领取，跨存储部分成功的正确性依靠业务自身的重复执行安全与结果核验。无法证明旧写入已停止时会阻塞后续写操作，这是老板明确接受的取舍：宁可让同步与索引等待，也不用心跳过期自动解锁——那会在远端写入其实还没结束时放进第二个写者。升级期间必须停用旧的 APScheduler 与常驻消费者，否则两套入口会同时处理同一批业务。
