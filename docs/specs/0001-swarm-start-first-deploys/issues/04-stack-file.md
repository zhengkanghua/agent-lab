# 04: 编排文件改成栈形态并按承诺加结构断言

**要交付什么：** 三个进程的服务定义从「普通容器编排」改成「栈编排」：容器名这类字段换成栈认识的写法，重启语义、副本数、健康检查（指向就绪端点并带一组初值参数）、每服务各自的更新顺序与停止宽限、以及一张声明为外部存在的项目网络。同时补一组结构断言，专门守住那些「删掉不会有任何测试变红、只在真机上以停机／重复投递／回滚失效的形式暴露」的声明。

**被谁阻塞：** 02: 建网并接通 Redis（一次性准备）、03: 把「能服务」与「已就绪」分开

**前置事实（工单 01 实测，施工时直接采用）：**

- 在 Swarm 里容器健康检查不过 = agent 把容器杀掉重启（`Failed ... "task: non-zero exit (137): dockerexec: unhealthy container"`），不是 compose 里那种「只显示 unhealthy」。**老板已拍板：只在 API 上声明健康检查（指向 `/ready`）；Beat/Worker 不写 `healthcheck`**——它们的检查由部署工作流用 `docker exec` 打到任务上，避免 Worker 被健康检查杀掉、中断手上的文档批次。
- 定义了健康检查的任务，在健康检查通过之前 `docker service ps` 显示 `Starting`（不是 `Running`），**且收不到流量**（实测窗口内 980 次请求 0 失败、最慢 3ms）。
- 三个容器内依赖是 `redis`、`postgresql`、`minio`（不是规格写的只有 Redis），`REDIS_URL`/`DATABASE_URL`/`LLMOPS_DATABASE_URL`/`S3_ENDPOINT` 都按容器名解析，接网后不用改 `.env`。
- 两个相对路径挂载都去掉了：`/data` 没有被任何代码引用（服务器上那个目录一直是空的），`.env` 的键统一由 `env_file` 注入（实测可用）。

**状态：** 已完成（文件、结构断言与真机核对都过了）

- [x] 编排文件通过栈加载器的校验，且不含栈不接受或会静默忽略的写法（容器名、环境文件、相对路径挂载这类都以显式写法表达）
- [x] 从文件里能读出：三个服务各自的更新顺序与停止宽限、健康检查与其时间参数、网络声明为外部、重启语义与副本数
- [x] 结构断言在关键声明被删时变红，至少覆盖四处：切换时不停机所依赖的「先起后停」、周期任务不重复投递所依赖的「先停后起」、三个服务各自的停止宽限、镜像清理不再是「无条件清空」
- [x] 断言只判「声明是否存在及其取值」，不比对整段文本——同一个承诺换一种等价写法时断言仍然为真

### 验收记录（2026-10-01 实测，服务器 `instance-20260723-1451`）

**文件与断言。** 新增 `backend/docker-stack.yml`（栈形态）；新增 `backend/tests/test_deploy_stack_file.py` 10 条结构断言（YAML 解析 + 时长归一化，`180s` 与 `3m` 等价）；`backend/docker-compose.yml` 保留作退回旧方式的逃生路径，头部已标注它不再是日常部署用的那一份；部署工作流里那句 `docker image prune -f` 按本工单要求改成 `docker image prune -f --filter "until=168h"`（工单 05 保留它）。

**G1 校验通过。** `docker stack config -c docker-stack.next.yml`（先从 `.env` 导出那六个会被插值的键）输出 309 行，三个服务的声明都在：backend `order: start-first` / `parallelism: 1` / `failure_action: rollback` / `monitor: 1m0s` / `replicas: 1` / `condition: any` / `stop_grace_period: 3m0s`（180s）；beat `stop-first` / `30s`；worker `start-first` / `6m0s`（360s）。healthcheck 一共只出现一处（旧 compose 里那两条 beat/worker 健康检查已经不在了），参数为 `interval 5s / timeout 3s / retries 3 / start_period 15s`；端口 `published: 18000` + `mode: ingress`；外部网络带写死的 `name: agent-lab-net` + `external: true`。

**栈加载器接受程度的真机核对**（惰性探针栈，语法与候选文件相同，容器只 `sleep`）：`env_file: [.env]` 真的把键注入服务定义与容器（`REDIS_URL`/`TZ`/`WORKER_COUNT` 都在，`.env` 里没有 `TASK_WORKER_CONCURRENCY` 于是回落到 `:-2`）；`x-` 锚点 + `<<` 合并键可用；`healthcheck` 的 `StartPeriod=15000000000`（15s）落进定义；`mode: ingress` 与 `PublishedPort` 正确；`logging` 的 `max-size/max-file` 落进 `LogDriver`；`stop_grace_period` 落成 `TaskTemplate.StopGracePeriod`（180s → `180000000000`、30s → `30000000000`，锤点形态下也一样）；`replicas` 落成 `{"Replicated":{"Replicas":1}}`。

**结构断言的反向验证。** 逐个改坏七处（API 先起后停→先停后起、Beat 先停后起→先起后停、API 宽限 180s→30s、镜像清理去掉 `until`、健康检查 `/ready`→`/health`、加 `container_name`、给 Beat 加健康检查），**七处全部变红**；还原后 10 条重新全绿。

**三条量出来的坑（已交给工单 05，并要在工单 10 的手册里写清）：**

1. `docker stack config` 与 `docker stack deploy` **不读 `.env`**（只有 `docker compose` 读）：文件里 `${VAR}` 引用的键必须先导成进程环境，否则写成 `:?` 的键会直接报错（响的，不是静默错值）。不能整份 `source .env`：里面 `LLM_USER_AGENT` 这类值带空格与括号，会被 shell 拆坏。
2. `docker stack config` **不是「swarm 会不会接受」的校验**，它只做合并 + 插值：带着 `container_name`、`restart:`、相对路径挂载的旧 compose 文件被它一声不响地放过了。所以「不许出现栈会静默忽略的写法」只能靠上面那组结构断言守。
3. `deploy.stop_grace_period` 不是合法字段（`Additional property stop_grace_period is not allowed`），服务级那一处才是唯一写法；另外 `env_file` 的路径按**编排文件所在目录**解析，不按当前目录。

**一条读取方式的坑（写给工单 06–09 的演练脚本）。** `docker service inspect --format '{{.Spec.TaskTemplate.StopGracePeriod}}'` 会报 `map has no entry for key`，而 `--format '{{json .Spec.TaskTemplate}}'` + grep 读得出同一个值——读服务定义时用后者（`UpdateConfig`、`Mode` 这些也是同理）。
