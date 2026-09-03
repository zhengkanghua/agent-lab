# 定时任务：进程内 APScheduler，数据库为唯一事实来源

后端需要一个定时任务模块：FreshRSS 增量同步进 PostgreSQL、PostgreSQL 待索引文档写进 Qdrant，
都要按 cron 周期自动执行；cron 和参数由超级用户在前端管理端配置。此前 `main.py` 明确声明
「不实现自动调度或后台任务」，本 ADR 记录推翻这一立场的原因和新边界。

## Decision

1. **调度引擎用 APScheduler 3.x（AsyncIOScheduler），跑在现有 backend 的 uvicorn 进程内**，
   不引入 Redis/Celery 等消息中间件，也不新增第二个进程。
2. **调度器只用内存 job store；任务定义与执行历史都存我们自己的两张表**
   （`scheduled_job` / `scheduled_job_run`），PostgreSQL 是唯一事实来源。进程启动时从表里加载
   启用中的任务，此后由管理 API 增删改查并实时作用于调度器。
3. **cron 字符串按 `SCHEDULER_TIMEZONE`（默认 Asia/Shanghai）解释**；翻译出的具体时刻一律以
   UTC 存库，数据库存储时区约定（`DATABASE_TIMEZONE=UTC`）不变。
4. **任务类型由代码注册**，v1 只有 `freshrss_sync` 与 `index_pending`，直接复用
   `NewsPipelineExecutionService` 的对应执行器；执行时按请求新建写 Runtime、用完即关，
   与手动 `POST /pipeline/run-once` 的资源生命周期一致。
5. **运行策略**：同一任务上一轮未结束则跳过本轮（不排队）；错过执行点给 10 分钟宽限补跑；
   失败不自动重试（下一轮 cron 自然重试，另有手动触发兜底）；执行历史每任务只留最近 50 条。
6. **调度器受 `SCHEDULER_ENABLED` 总开关控制，默认关闭**，生产由服务器 `.env` 显式打开；
   离线测试天然不带调度器。种子任务（两条、启用）由 Alembic 迁移插入。

## Considered Options

**Celery Beat / Arq 等任务队列。** 能力最全，但都要求先引入 Redis/RabbitMQ。本项目单机单容器、
任务只有两三类、执行器本身已经是数据库认领式（多进程并发天然安全），为两三个 cron 上一个
消息系统是纯运维负担，没有对应收益。

**独立调度进程（同镜像第二个 compose 服务，`agent-lab scheduler` 子命令）。** 故障隔离更好：
部署重启 API 不会打断正在跑的任务，CPU 计算也不会与 SSE 共享事件循环。但多一个进程要看护、
要配健康检查，docker-compose 与部署文档都要动；而本项目的量级（单人内部系统、批次有上限）
用不到这层隔离。共享事件循环的 CPU 毛刺（HTML 解析、切块、tiktoken）用
`asyncio.to_thread` 把切块计算移出事件循环来缓解，对手动流水线同样生效。将来真要多实例部署，
迁移路径是现成的：把同一个调度服务从 lifespan 挪到 CLI 子命令即可，任务执行不受影响。

**APScheduler 自带的 SQLAlchemyJobStore。** 省一张任务表，但表结构是它的内部格式（任务被
pickle 序列化成二进制），管理端没法展示与编辑，也没有执行历史——而「方便管理」正是本次需求。

**自己写 asyncio 循环 + croniter。** 依赖最少，但下次执行时间计算、misfire 补跑、单任务互斥
这些易错细节要全部自造自测；APScheduler 3.x 是久经考验的成熟实现，没必要重造。

## Consequences

- **单进程是硬前提**：调度器在进程内意味着部署必须保持单 uvicorn worker、单实例。改成多
  worker 或多实例前，必须先迁到独立调度进程（或给任务执行加数据库租约），否则同一任务会被
  重复调度。`docs/container_deployment.md` 与 backend README 需写明这条约束。
- **每次部署重启会打断正在跑的任务执行**。可接受：执行器是有界且可恢复的（来源同步有
  checkpoint、索引认领超时会回收重排），被打断的批次会在下一轮或超时回收后继续。
- **生产 `.env` 需要新增 `SCHEDULER_ENABLED=true`**，忘加的表现是「一切正常但任务不跑」。
  部署文档必须列出该变量。
- 历史清理（每任务 50 条）在每次执行结束后顺手做，不单设维护任务。
- `CONTEXT.md` 词条：定时任务、任务类型、任务执行。
