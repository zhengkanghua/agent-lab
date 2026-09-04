# 调度器跑在独立进程，backend 多 worker 化

## Context

ADR 0014 决定调度器（APScheduler AsyncIOScheduler）跑在 backend 的 uvicorn 进程内，前提是部署
保持单 worker 单实例，并在否决「独立调度进程」时预留了迁移路径：「把同一个调度服务从 lifespan 挪到
CLI 子命令即可」。随后 API 需要多 worker（`WORKER_COUNT` 默认 2）提升吞吐，多 worker 下每个进程
都会加载一份进程内调度器，同一任务会被重复调度——0014 的「单进程硬前提」被打破，按它自己预留的
路径完成迁移。本文记录迁移后的形态。

## Decision

1. **生产容器部署下，调度由独立进程承担**：同镜像第二个 compose 服务 `scheduler`，入口
   `python -m agent_lab.scheduler_main`（`backend/src/agent_lab/scheduler_main.py`）；backend
   容器在 compose 里强制 `SCHEDULER_ENABLED="false"`，scheduler 容器强制 `"true"`——防呆放在
   编排层，不依赖部署者记得改 `.env`。`main.py` lifespan 的进程内调度路径保留，降级为本地开发的
   裸进程选项；**该模式下 0014 的「单 worker 单实例」约束继续成立**，多 worker 必须用独立进程。
2. **0014 的其余决策全部沿用**：内存 job store；`scheduled_job` / `scheduled_job_run` 两张表、
   PostgreSQL 是唯一事实来源；cron 按 `SCHEDULER_TIMEZONE`（默认 Asia/Shanghai）解释、具体时刻以
   UTC 存库；任务类型由代码注册（`freshrss_sync` / `index_pending`）；同一任务上一轮未结束跳过、
   10 分钟宽限补跑、失败不自动重试、历史每任务 50 条的运行策略；种子任务由 Alembic 迁移插入。
3. **`SCHEDULER_ENABLED` 语义收窄**：容器部署下该值由 compose 的 `environment` 覆盖，`.env` 里写
   它对 backend 容器不生效；它只对非容器的裸进程部署（本地开发）有意义。文档不得再要求
   「生产 `.env` 必须写 `SCHEDULER_ENABLED=true`」。

## Considered Options

**多 worker + 数据库租约，调度器仍进程内。** 每个 backend 进程都跑调度循环，靠租约表保证同一时刻
只有一个实例真正执行。不用第二个进程，但要自造租约的获取/续期/超时回收逻辑并测试，出错模式
（租约持有者崩溃后多久释放）难验证；而 0014 已把迁移路径留好——调度服务本就是独立构造的
`ScheduledJobRunner`，独立进程是零业务代码改动的方案。

**消息队列（Redis / Celery Beat）。** 0014 已否决，理由不变：两三类 cron 不值得引入消息中间件。

## Consequences

- **部署是两个容器**（backend + scheduler），同镜像不同 command；scheduler 无端口、无 HTTP，
  与 backend 并行启动、互不 depends_on。backend 的 `WORKER_COUNT` 只影响 uvicorn worker 数，
  与调度器无关。
- 「每次部署重启会打断正在跑的任务执行」这一后果不变，只是从「重启 backend」变成「重启 scheduler」；
  执行器有界且可恢复（来源同步有 checkpoint、索引认领超时会回收），被打断的批次会续上。
- **已知复制债**：`scheduler_main.py` 的 `build_pipeline_write_runtime` / `build_scheduler_runner`
  与 `main.py` 的 lifespan 构造逻辑重复（代码注释自认「复制自 main.py」）。合并进共享模块前，
  改其中一处要人工同步另一处。
- 文档同步：`docs/container_deployment.md`、`backend/README.md`、`backend/docs/architecture.md`
  的调度器表述以本文为准。