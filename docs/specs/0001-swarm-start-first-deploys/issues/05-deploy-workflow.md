# 05: 部署工作流改成新流程

**要交付什么：** 部署命令的顺序与实现从「停旧 → 迁移 → 起新 → 等健康」改成新形态下的正确顺序：候选栈文件上传与校验 → 在项目网络上跑一次性容器（Redis 自检、用量库配置自检、迁移、建表初始化）→ 停旧容器 → 起栈 → 等就绪。同时：镜像清理改成只删 7 天前的无名镜像（保住可回滚的上一版），Beat/Worker 的检查打到任务而不是容器名。改完先用占位服务把这套顺序手工复演一遍，不碰生产。

**被谁阻塞：** 02: 建网并接通 Redis（一次性准备）、04: 编排文件改成栈形态并按承诺加结构断言、03: 把「能服务」与「已就绪」分开（部署后要等它报就绪）

**状态：** 进行中（工作流已改写；占位复演待服务器执行）

**已落进工作流的实测约束（工单 04 量出来的）：**

1. `docker stack deploy` **不读 `.env`**，所以部署脚本在起栈前先把会被插值的六个键从 `.env` 导出成进程环境（不整份 `source`：`LLM_USER_AGENT` 这类值会被 shell 拆坏），其余键由服务定义里的 `env_file` 注入容器。
2. 部署命令必须带 `--with-registry-auth`（否则服务定义里只有可变标签，秒级回滚是假的），并显式 `--detach=false` 等更新器给出结论。
3. 一次性容器从 `docker compose run` 改成 `docker run --network agent-lab-net --env-file .env`，与服务看到同一个世界。
4. ACR 登录改成「传了凭据就登，没传就沿用机器上已有的登录」，使同一段脚本手工也能跑（复演与手册都靠这一条）。
5. 就绪判定两条一起看：更新器的结论（`UpdateStatus.State`）与任务本身（副本数 + API 容器健康）；只看到「任务健康」不够——回滚后跑的正是旧任务，它当然健康。

- [x] 在真机上用占位服务把新顺序手工复演一遍跑通：网络已存在的前提下，一次性容器（自检、迁移、初始化）先跑完、再停旧、再起栈、再等就绪，全程没有手工补步骤
- [ ] 一次性容器都跑在项目网络上；Redis 连接自检失败时部署在「停旧」之前终止，正在运行的服务不受影响
- [x] 镜像清理只删 7 天前的无名镜像；一次部署后，被顶替下来的上一版镜像仍在本地
- [x] 服务的健康/就绪检查打在任务上，服务重建或扩副本后依然有效
- [ ] 候选文件非法或任一自检失败时，工作流以非零退出且不改动正在运行的服务

### 验收记录（2026-10-01 实测，服务器 `instance-20260723-1451`）

**顺利路径复演（退出码 0，跑的是从工作流里逐字取出的那段脚本）**：栈文件校验 → 六个键导出 → `docker pull` → 真一次性容器（Redis 自检通过、用量库配置自检通过、`alembic upgrade head`、建用量库与用量库迁移、`init-checkpointer` 返回 `{"ok": true}`）→ 停占位旧容器（`Exited`）→ 起栈 → 等就绪。输出里四条关键证据：

- 镜像引用是 `.../agent-lab:backend-latest@sha256:2915f9b8…`——**`--with-registry-auth` 真的把摘要钉住了**（不带它就只有可变标签）。
- 三个占位的 `StopGracePeriod` 分别是 `180000000000` / `30000000000` / `360000000000`（180s / 30s / 360s），与文件里各自声明的完全一致。
- `新任务已就绪：Up 3 minutes (healthy)（运行中任务 3 个）`，随后 Beat 与 Worker 两条检查打在**任务容器**上通过（`worker@...: OK / pong`）。
- 镜像清理 `Total reclaimed space: 0B`，且 `placeholder:v1/v2` 与 `backend-latest` 都还在本地——**可回滚的上一版确实被保住了**（工单 09 要用到它）。
- 全程三个生产容器 `Up 15–16 hours` 未变。

**复演抓到的一个真 bug（已修，必须记下）**：导出 `.env` 插值键那段原本写的是
`value="$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- | tr -d '\r')"`，在 `set -euo pipefail` 下，
`grep` 找不到键（`.env` 里确实没有 `TASK_WORKER_CONCURRENCY`）会让整条管道返回 1，而「赋值给变量的命令替换失败」同样触发 `set -e` —— 脚本在 `docker pull` 之后当场退出（退出码 1）。这个 bug 一定会带到工单 06 的真部署上，是复演把它挡在真机之前。修法是命令替换里加 `|| true`；同时给就绪等待里那条 `docker service` 循环也加上容错（第一个轮次服务还没创建时那种失败不该把脚本带走）。

**两条读服务定义的小坑（写给工单 06–09 的演练脚本）**：

- `UpdateConfig` 在 **`Spec.UpdateConfig`**（ServiceSpec 的字段），不在 `TaskTemplate` 下——按 `TaskTemplate.UpdateConfig` 读会报 `map has no entry`。
- 点号写法对某些字段（如 `StopGracePeriod`）会报同样的错，用 `--format '{{json .X.Y}}'` + grep 读得出。

**`--detach=false` 的噪声**：非 TTY 下 CLI 会把「还在等收敛」重复打印三百多遍（三个服务加起来近千行），已改成过滤 `^verify: Waiting` 但用 `PIPESTATUS` 单独取退出码，避免真出错时被噪声埋掉。

### 失败演练（一份脚本两次故意弄坏，2026-10-01 实测）

- `J1` 把 scratch 的 `REDIS_URL` 指向不存在的地址 → 日志尾部出现 `Redis 连接检查失败（ConnectionError）`，**在「停掉旧编排」之前就退出**（该行出现 0 次），占位旧容器仍在跑、没有留下栈、生产容器未变。
- `J2` 把候选文件写成 `deploy.stop_grace_period`（加载器拒绝的写法）→ 校验那一步就失败（`services.backend.deploy Additional property stop_grace_period is not allowed`），日志里连 `Redis 连接检查通过` 都没有。

### 复演与演练一共挡住三个会在真部署上直接炸的问题

这正是规格要求「先用占位服务复演、不碰生产」的价值：

1. `set -euo pipefail` 下 `grep` 找不到键会让命令替换失败、脚本当场退出（`.env` 里确实没有 `TASK_WORKER_CONCURRENCY`）。
2. `docker pull "$BACKEND_IMAGE"` 排在「从 .env 导出插值键」之前，而 CI 的 `envs:` 并不传这个变量 → 远端脚本 `unbound variable`、部署在第一分钟就红。已调顺序，并新增一条「CI 构建推送的镜像地址必须等于服务器 `.env` 里那一份」的显式检查（不一致的症状是「部署全绿但跑的还是旧镜像」）。
3. **heredoc 型的一次性容器少了 `-i`**：`docker run ... --entrypoint python "$IMG" - <<'PY'` 少了 `-i` 时 CLI 不会把 stdin 交给容器，容器里的 `python -` 读到空程序、立刻退出 0、什么都不打印 —— **三条自检（Redis、用量库配置、建用量库）全部空跑而脚本看不出异常**。第一轮演练“J1 通过”因此是假的（它根本没到 Redis 自检）；旧流程用 `docker compose run` 默认接 stdin，所以那时没这个问题。三处已加 `-i` 并写清原因，重跑后 `J1` 才真地在自检处停下。

### 留给工单 06 的一个未决问题

演练期间生产那三个普通容器被**重建**过一次（`Created=2026-10-01T03:00:45`，事件里带 `com.docker.compose.replace=…` 标签，即有人跑过 `docker compose up -d`）。`.env` 没被改过、服务已恢复健康、`agent-lab-net` 三根线复核全部 OK，所以不影响施工；但**切换前必须确认面板不会再自动重建这三个旧容器**——否则它会和 Swarm 服务争 18000 端口。
