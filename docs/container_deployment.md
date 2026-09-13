# 容器化部署：GitHub Actions + 阿里云 ACR + 甲骨文

本文是当前部署方式的操作手册：后端跑在容器里，前端是纯静态文件。取舍理由见
[ADR 0008](adr/0008-backend-in-image-frontend-as-static-files.md)。与容器化无关的
Cloudflare 与账号管理内容收在本文第五节。

```text
浏览器
  → Cloudflare
  → OpenResty（TLS、反代、限流、静态站）        ← 自行配置，本文不提供配置
      ├─ /        → <WEB_ROOT>   dist 静态文件
      └─ /api/*   → 127.0.0.1:18000（去掉 /api 前缀）        backend 容器
                                                      task-beat 容器（周期受理与维护）
                                                      task-worker 容器（后台业务执行）
                                                      redis 容器（项目共用中间件与持久盘）
                      → 远程 PostgreSQL / Ollama / Qdrant / FreshRSS / MinIO(S3)
```

前端由 OpenResty 提供静态文件。`backend`、`task-beat` 和 `task-worker` 使用同一个后端镜像，项目共用的 `redis` 使用 Redis 镜像，也可通过连接配置使用已有实例。API 校验权限、持久受理并查询；单个 Beat 推进周期和补投；Linux prefork Worker 完成三类周期任务、文档处理批次和 HTTP Pipeline。PostgreSQL 保存状态和结果，Redis 当前传递任务消息，后续缓存等用途共用连接并区分键前缀。文件与 FreshRSS 的文档待办不需要额外 cron，进程职责与恢复决策见 [ADR 0019](adr/0019-scheduled-execution-and-write-coordination.md)。

发布工作流在切换后检查 API 健康、Beat 推进和 Worker 消息连接，通过后才上传前端。隔离验收在镜像构建和实际切换之前执行。

## 与容器化无关的内容

Cloudflare 与账号管理收在本文第五节；网关侧要求（登录限流、请求体大小、SSE 读超时）见
[`backend/README.md`](../backend/README.md) 的「生产前置要求」一节——服务本身不做这些，
需要在 OpenResty 侧落实。

## 一、服务器一次性准备

以下步骤只在第一次部署前做一遍。**示例中的路径、用户名和 registry 地址都是占位符，请替换
为实际值**：部署目录以 `/opt/agent-lab` 为例、静态站目录以
`/opt/1panel/www/sites/<站点名>/index` 为例、部署用户以 `deploy` 为例。

本仓库是公开仓库，所以真实的 registry 地址、部署路径都不写在代码和文档里，而是放在
GitHub Variables（CI 侧）和服务器 `.env`（运行侧）。三者的对应关系见第 7 节。

### 1. 建部署用户并加入 docker 组

CI 用这个账号 SSH 进来跑 `docker compose` 和接收 dist。不用 root：那台机器上还有其他
服务，CI 私钥一旦泄露不该等于全机权限。

```bash
# 建无密码登录、无 shell 交互需求的系统用户（保留 shell，SSH 执行命令需要它）
sudo useradd --create-home --shell /bin/bash deploy

# 加入 docker 组，这样跑 docker 命令不需要 sudo
sudo usermod -aG docker deploy

# 确认生效（应输出包含 docker 的组列表）
id deploy
```

> `docker` 组成员等价于 root 权限（可以挂载宿主机任意目录到容器里）。这是使用 Docker
> 的固有前提，不是本方案引入的。真要进一步收紧需要 rootless Docker，那是另一个话题。

### 2. 配置 SSH 密钥

在**本地**生成一对专用密钥（不要复用你平时登录用的那把）：

```bash
ssh-keygen -t ed25519 -C "github-actions-agent-lab" -f ~/.ssh/agent_lab_deploy
```

公钥装到服务器：

```bash
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy chmod 700 /home/deploy/.ssh
# 把 ~/.ssh/agent_lab_deploy.pub 的内容追加进去
sudo -u deploy tee -a /home/deploy/.ssh/authorized_keys < /path/to/agent_lab_deploy.pub
sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys
```

**私钥**（`~/.ssh/agent_lab_deploy`，不带 `.pub` 的那个）全文填进 GitHub Secrets 的
`SSH_KEY`，包括 `-----BEGIN ...-----` 和 `-----END ...-----` 两行。

本地验证一次能登进去：

```bash
ssh -i ~/.ssh/agent_lab_deploy deploy@<甲骨文公网IP> "docker ps"
```

这条命令必须成功。它同时验证了三件事：密钥对能用、`deploy` 用户存在、docker 组生效。

### 3. 建部署目录，放 compose 文件与 .env

```bash
sudo mkdir -p /opt/agent-lab
sudo chown deploy:deploy /opt/agent-lab
```

全新环境把仓库 `backend/docker-compose.yml` 复制到 `<DEPLOY_DIR>/docker-compose.yml`，与 `.env` 放在同一目录。已有环境保留旧文件，发布工作流上传 `docker-compose.next.yml`，以旧编排停用旧入口、迁移成功后再替换，避免先覆盖文件导致旧 scheduler 无法识别。该编排继续使用已有外部 `1panel-network`。

候选文件的上传和替换要求部署账号能写项目目录。工作流先核对目录内已有编排和 `.env`；目录不可写时，复用现有后端镜像启动一个无网络的临时容器，仅将项目目录本身的属主改为部署账号，与上面的建目录步骤一致。不递归修改目录内文件的权限，也不改变上层 1Panel 目录。

照 [`backend/.env.example`](../backend/.env.example) 建 `<DEPLOY_DIR>/.env`：

```bash
sudo -u deploy vi <DEPLOY_DIR>/.env
sudo chmod 600 <DEPLOY_DIR>/.env
sudo chown deploy:deploy <DEPLOY_DIR>/.env
```

**直接在服务器上用编辑器写这个文件，不要在 Windows 上写好再上传。** Windows 编辑器保存的是
CRLF 行尾，而 `.env` 不经过 Git（`.gitattributes` 管不到它），`\r` 会留在值的末尾——密码、
URL、API Key 后面多一个看不见的字符。这类故障很难查：日志里的报错看起来像密码错或地址错，
但值「看上去」完全正确。

填写时注意以下配置：

1. `AUTH_COOKIE_SECURE=true`（生产走 HTTPS，必须）。
2. `DATABASE_URL` 指远程库。**不要**写 `localhost`——容器里的 `localhost` 指容器自己，
   不是宿主机。
3. `AUTH_ADMIN_EMAIL` 与 `AUTH_ADMIN_PASSWORD` 必须同时存在，密码 12–128 字符、不能与
   邮箱相同。模板里的尖括号是占位符，必须替换。
4. **不要写 `LLM_CHECKPOINT_POOL_SIZE`**。它在 `config/llm.py` 里声明为 `strict=True`，
   而 compose 的 `env_file` 注入的一律是字符串，配上会让容器启动即 `ValidationError`。
5. 删除已失效的 `SCHEDULER_ENABLED`、刷新间隔、关闭宽限及按条数保留历史的旧设置。保留 `SCHEDULER_TIMEZONE=Asia/Shanghai` 与 `DATABASE_TIMEZONE=UTC`；周期、参数和启停仍在任务管理配置，默认重试与历史保留通过网页策略管理。
6. 文档原件必须配置 `S3_ENDPOINT`、`S3_BUCKET`、`S3_REGION`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`
   和 `S3_ADDRESSING_STYLE`。MinIO endpoint 是后端可达的 API 地址，不是管理控制台地址。
   保持 `S3_REQUIRED=true`；缺少原件存储时上传和 FreshRSS 接收不能成功，不回退旧处理链。
7. 新版使用 `QDRANT_COLLECTION_SCHEMA_VERSION=v3`。镜像已设置
   `DOCUMENT_TOKENIZER_PATH=/app/resources/tokenizers/bge-m3`，通常无需在 `.env` 重复设置；
   不要用本地开发的 `.cache/...` 路径覆盖它。`DOCUMENT_CHUNK_MAX_TOKENS` 默认 512，包含标题上下文。
8. 容器默认 `REDIS_URL=redis://agent-lab-redis:6379/0`，该别名只属于内部网络，避免与共享 `1panel-network` 中的 `redis` 同名服务冲突；不要复制原生开发的 `127.0.0.1` 地址。自管 Redis 时显式设置 URL，密码单独填 `REDIS_PASSWORD`，留空表示不需要密码，不把密码拼入 URL。API、Beat、Worker 的 Redis 配置与 `TASK_QUEUE_NAME` 必须一致；队列名同时决定消息及辅助键的前缀，不同环境须区分。连接凭据只放服务端配置，不放任务参数或前端变量。
9. `WORKER_COUNT` 是 API 进程数，`TASK_WORKER_CONCURRENCY` 是每个 Worker 的 prefork 子进程数。`docker compose up -d --scale task-worker=2` 可增加 Worker 实例，Beat 保持单个。容器关闭宽限用于等待当前工作，不能据此限制清理整次时长。

编排自带 Redis 只接内部 `middleware` 网络、无宿主端口，启用 AOF/everysec、持久卷 `redis-data` 和 `noeviction`；容量由 `REDIS_MAXMEMORY` 设置，自管实例在 Redis 服务端配置。`REDIS_URL` 为项目公共连接，任务消息和后续缓存按各自前缀区分；`noeviction` 作用于整个实例，缓存用 TTL 过期，内存满时新增写入失败。任务发布失败的依据保留在 PostgreSQL，等待补投。运维监控需关注内存占用/上限、AOF 写入状态、持久卷剩余空间和任务投递错误；在现有监控平台配置告警，具体阈值按批准容量设置。Redis 重启不清卷，不对共享服务执行 FLUSHDB 或故障实验。

原件桶需预先创建并保持私有，按环境隔离。应用凭据只需覆盖本项目对象的读取、条件写入和删除；
开启桶版本控制时也要允许读取和删除具体对象版本。应用不会创建桶或修改桶配置。不要为原件启用
对象锁，也不要配置会绕过应用保留规则的桶级到期清理：待审核、失败、拒绝资料及已采用历史仍需保留。
浏览器通过后端鉴权下载原件，无需桶公开读权限或浏览器侧 S3 密钥。

构建环境首次安装依赖并从 Hugging Face 下载锁定的 tokenizer 文件，校验 SHA-256 后放入镜像，
不下载 Embedding 模型权重。CI 在结构测试前也显式准备这些资源；运行时只读本地文件。
部署时可先用新镜像做离线资源检查：

```bash
docker compose run --rm backend python -m agent_lab.prepare_document_resources --check
```

这条检查不连接数据库、S3 或模型服务。真实 S3 的字节读写、幂等和版本删除验收入口见
[后端测试说明](../backend/README.md#测试)，只使用随机测试键；资源检查或 API `/health` 成功
不能代替原件存储验收。

### 4. 让 deploy 用户能写静态站目录

CI 用 rsync 把 dist 传到 1Panel 的站点目录。该目录默认属主通常不是 `deploy`，先确认：

```bash
ls -ld <WEB_ROOT>
```

如果属主不是 `deploy`，改掉：

```bash
sudo chown -R deploy:deploy <WEB_ROOT>
```

> 若 1Panel 面板后续操作会把属主改回去，改为把 `deploy` 加入该目录原属主的组，并给组
> 写权限（`sudo chmod -R g+w`），避免和面板互相打架。

### 5. 确认端口未被占用

默认后端映射到宿主机 `18000`。确认它是空的：

```bash
ss -tlnp | grep :18000
```

有输出说明被占用，改 `<DEPLOY_DIR>/.env` 里的 `BACKEND_PORT`，并同步改 OpenResty 的
反代目标。

### 6. 配置 OpenResty

自行配置，本文不提供配置文件。需要落实的几件事：

- `/` 指向 `<WEB_ROOT>`，并且**必须有 `try_files` 回落到
  `index.html`**。前端是 History 模式路由（`frontend/src/app/router.ts`），少了这条，
  用户在 `/admin/users` 按 F5 刷新会 404 白屏。就是这一句：

  ```nginx
  location / {
      try_files $uri $uri/ /index.html;
  }
  ```

  三件事按顺序说清楚，踩过一次就知道为什么要写下来：

  1. **它必须排在 `/api/` 那条反代之后。** 反过来的话 API 请求会被这条先接住，
     前端收到的是一坨 HTML 而不是 JSON，表现成「接口全挂但服务器日志一切正常」。
  2. **1Panel 伪静态里那些预设一个都不要选。** `wordpress`、`laravel`、`thinkphp`、
     `discuz` 这些是把 URL 重写到 `index.php` 的 PHP 方案，和本项目无关。列表里有
     `vue` / `react` / `spa` / `history` 这类条目就选它，没有就选空白项把上面那句贴进去。
  3. **可能和站点配置里已有的 `location /` 撞车。** 伪静态是被 `include` 进站点配置的，
     两个 `location /` 同时存在时 OpenResty 起不来，`nginx -t` 会直接报 duplicate location。
     撞了就别用伪静态框，改成直接编辑站点配置文件，把原有那个 `location /` 的内容换掉。

  配完两处都要验：先 `nginx -t` 确认配置没坏再 reload，然后在浏览器里刷新
  `/admin/users` 和一个真实的 `/agent/<会话id>`，两个都不 404 才算成。第二个不能省——
  它那段路径是运行时产生的 UUID，能覆盖到「服务器上永远不存在对应文件」这种情况。

  顺带记下两条已经评估并否决的替代方案，免得下次重新推导：**Hash 模式**（URL 变成
  `/#/admin/users`，服务器只被要根路径）是用 URL 变丑换配置省事，且与
  [ADR 0008](adr/0008-backend-in-image-frontend-as-static-files.md) 的既定前提冲突；
  **构建时预渲染**要求为每个 URL 生成真实文件，而会话 id 构建时不可知，
  `/agent/:threadId` 刷新照样 404，绕一圈还是要回来配这条。
- `/api/` 反代到 `127.0.0.1:18000`，并**去掉 `/api` 前缀**（后端路由本身没有这个前缀）。
- `/api/agent/chat` 是 SSE 长连接，读超时要放开（参考值 180s）。后端已自带
  `X-Accel-Buffering: no` 和心跳，不需要额外关缓冲，但读超时不能短于心跳间隔。
- 登录限流、请求体大小上限：见 `backend/README.md` 的「生产前置要求」。
- 有 Cloudflare 时，限流要配 `real_ip` 只信任 Cloudflare IP 段的 `CF-Connecting-IP`，
  否则限流按 Cloudflare 节点聚合而不是按用户。**没有 Cloudflare 时绝对不要配这一项**，
  那会让任何人自带假 header 就能绕过限流。

### 7. 配置 GitHub Secrets 与 Variables

都在同一个页面：仓库 → Settings → Secrets and variables → Actions。**注意那里有两个页签**，
Secrets 和 Variables 填在不同页签里，填错地方工作流读不到（读到的是空字符串）。

**Secrets 页签**（值加密，运行日志里显示为 `***`）——只放凭据：

| 名字 | 值 |
|---|---|
| `ACR_USERNAME` | ACR 用户名 |
| `ACR_PASSWORD` | ACR 固定密码（ACR 控制台 → 访问凭证 → 设置固定密码，**不是**阿里云账号密码） |
| `SSH_HOST` | 服务器公网 IP |
| `SSH_USER` | 部署用户名，如 `deploy` |
| `SSH_KEY` | 第 2 步生成的**私钥全文**，含 `-----BEGIN`／`-----END` 两行 |

**Variables 页签**（值不加密，日志里正常显示）——放地址和路径：

| 名字 | 值 | 从哪取 |
|---|---|---|
| `ACR_REGISTRY` | 如 `crpi-xxxx.ap-northeast-1.personal.cr.aliyuncs.com` | ACR 控制台镜像仓库详情页的「公网地址」 |
| `ACR_NAMESPACE` | 命名空间名 | 同上 |
| `ACR_REPOSITORY` | 仓库名，如 `agent-lab` | 同上 |
| `DEPLOY_DIR` | 如 `/opt/agent-lab` | 第 3 步建的目录 |
| `WEB_ROOT` | 静态站目录绝对路径，**不要带尾部斜杠** | 面板上站点详情页显示的目录 |

为什么地址用 Variables 而不是 Secrets：Secrets 的值在日志里会被打码，调 CI 时看到的是
`docker push ***/***:backend-latest`，出错几乎无法定位。这些值也不是凭据——光有地址没有
用户名密码拉不动私有仓库。

工作流第二步会检查这五个 Variables 是否都非空，缺了就在第一秒失败并指出缺哪个；不检查会
一路跑到几分钟后的 push 步骤才报一个含糊的错。

### 更换 registry 时必须同步改的地方

镜像地址现在存在**三个互相看不见的地方**，没有任何自动比对。改 registry、命名空间或仓库名时
必须三处一起改，漏一处的表现是「CI 全绿、容器也重启了，但跑的还是上一版代码」——没有报错。

| 位置 | 改什么 |
|---|---|
| GitHub Variables | `ACR_REGISTRY`、`ACR_NAMESPACE`、`ACR_REPOSITORY` |
| 服务器 `<DEPLOY_DIR>/.env` | `BACKEND_IMAGE` 整行 |
| 服务器 `docker login` | 重新登录新 registry（凭据存在 `~/.docker/config.json`） |

改完先手工验证一次再推代码：

```bash
cd /opt/agent-lab
docker compose config | grep image:      # 确认拼出来的地址是新的
docker compose pull                      # 确认拉得动
```

### 8. 首次部署前先手工验证一遍

不要指望第一次就让 CI 跑通。先在服务器上手工走一遍，把环境问题和 CI 问题分开：

```bash
cd /opt/agent-lab

# ACR 登录（会提示输密码；地址和用户名从 ACR 控制台取）
docker login <your-acr-registry> -u <your-acr-username>

# 此时 ACR 里还没有镜像，所以 pull 会失败——这是正常的。
# 先让 CI 跑一次，把镜像推上去，再回来执行下面的步骤。

docker compose pull
docker compose config --quiet
docker compose run --no-deps --rm backend alembic upgrade head
docker compose run --no-deps --rm backend agent-lab init-checkpointer
docker compose up -d redis backend task-worker task-beat
docker compose logs -f backend
```

## 二、日常部署

推代码到 `main` 即自动部署。也可以在 GitHub 的 Actions 页面手动触发
（`workflow_dispatch`），用于「服务器侧改了配置想重跑一次」而不必造空提交。

CI 的完整顺序在 [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) 里，
几个顺序约束是有意的，不要调整：

1. **测试在构建之前**：先以上次成功部署为基线选择受影响的检查；后端改动准备锁定 tokenizer 并运行离线回归，任务相关改动运行 Linux 核心验收，消息组件和迁移变化分别追加耗时兼容性与历史升级验收，前端使用 Vitest 选择受影响测试。范围与本地命令见 [后端 README](../backend/README.md#测试) 和 [前端 README](../frontend/README.md#验证)。手动触发或没有可靠基线时完整验证；
   所选检查失败就不构建、不推送、不部署。任务进程日志保存在 Actions artifact 中。
2. **迁移在 `up -d` 之前**，且 `alembic upgrade head` 在 `agent-lab init-checkpointer`
   之前。理由见 [ADR 0004](adr/0004-checkpointer-tables-outside-alembic.md)。
3. **后端部署在前端上传之前**：迁移失败时部署中止，前端仍是旧版本，不会出现「新前端调
   老后端」。
4. **`docker compose pull` 不能省**：tag 恒为 `backend-latest`，`up -d` 认为 tag 没变会
   直接复用本地旧镜像——表现是 CI 全绿、容器也重启了，但跑的还是上一版代码。
5. 候选编排先校验和拉镜像，再按旧编排停止 scheduler/Beat、API、Worker，保留 Redis 数据盘；成功迁移后替换编排并启动新进程。迁移失败保持停止，不能自动恢复旧协议继续写新版表。旧配置备份为 `docker-compose.previous.yml`。

推送完成后执行 `gh run list --limit 1` 核对最新部署，失败时用 `gh run view <run-id>` 查明原因；修复在本地验证后再推送。Actions 就绪检查覆盖 API、Beat 和 Worker 消息连接，业务验收仍需受控操作与执行编号。

## 三、排查

### Docling 首次切换与恢复

首次从旧文档链升级到 Docling 是一次资料切换，不能只更新镜像或把旧 Collection 改名为 v3。
旧 Point 没有候选索引身份，旧 Document 也没有可供采用或重建的冻结 Chunk。当前开发资料已获准
清空后重新导入，不建设旧正文迁移；以下顺序需记录实际执行结果，代码提交本身不表示切换已完成：

1. 准备私有 S3 桶、服务端配置、包含 tokenizer 的 ARM64 新镜像及迁移；先完成随机隔离资源验收。
2. 核对目标 PostgreSQL、当前项目的 Collection/Alias 与原件引用，列出待清理文档数量和来源范围。
   停止 backend、Beat、Worker、遗留 scheduler 及容器外 CLI，确认远端未决写入结束，避免旧版在迁移后继续写入。
3. 用新镜像执行 `alembic upgrade head`，按已核对范围清除文档、处理记录、已采用历史、审核记录、
   相关删除待办及对应索引和原件；只重置重新接收所必需的 Source checkpoint。
   保留账号、KnowledgeBase 配置、Source 绑定、定时任务配置、Agent 会话和 checkpointer 历史。
4. 使用 v3 的空目标启动新版 API、Beat、Worker 并连接项目共用 Redis，上传合成 MD/TXT 并接收范围内的 FreshRSS HTML。
   核对保存回执、原件下载、结构与 Chunk、正常自动采用、异常人工处理、旧版保留、检索和删除。
5. 记录实际清理与重新导入的数量及失败记录，再恢复日常入口。FreshRSS 沿用首次有界同步规则，
   重置 checkpoint 不保证回灌全部历史。存储或镜像未就绪时先保持切换未完成，不先清空资料。

日常重建只复用当前已采用版本的冻结 Chunk。需要改变正文、章节或切分规则时，应生成候选并重新采用。
重建新 generation 失败时保留原 Alias；发布结果不确定时先按下一节核实旧执行和写占用，再恢复发布：

```bash
docker compose run --rm backend agent-lab recover-index-rebuild --generation <目标代次>
```

该命令核对已发布或仍在原 Alias 的状态，不重新向量化。解析或采用失败在文档管理中重试、修正或拒绝；
拒绝只停止使用，明确删除才清除原件和文档历史。原件、向量删除中断保留待办，需继续核对并完成清理。

### 定时任务升级与恢复

执行本节操作前确认目标环境、备份和授权。共享任务迁移为 `a91b3c7d5e20 → b6e2f9047a31`，保留周期配置、旧执行身份、快照及业务待办，新增受理、投递、策略和原请求回执。旧记录没有参数快照时明确标记缺失，不用当前配置伪造失败参数；这类历史不能人工重试。旧执行中、已有未确认占用或标记待核实的记录转为待核实；同配置存在多条未确认旧执行时拒绝升级，须核实后处理，不能删历史凑约束。

首次切换的顺序：

1. 完成旧结构迁移、多进程 PostgreSQL、真实 Redis/prefork/Beat 与 PostgreSQL/Qdrant/S3 恢复验收，记录实际环境和缺失项。夹具及隔离命令见 [后端 README](../backend/README.md#测试)；普通离线测试、浏览器替身和 Celery eager 不能替代。
2. 准备新镜像、候选编排、共用 Redis 连接及持久化配置，保留旧编排和切换前数据库备份。原 v3 文档数据无需因共享任务重构而清空；不要把本次任务迁移与上一节 Docling 资料重置混在一起。
3. 在维护窗口先停止旧 scheduler 与 API 的后台入口，并停止容器外写 CLI，核实旧进程及远端未决写入已结束。工作流会按旧编排停止服务，但不能识别容器外 CLI 或替代远端核实。已经使用新布局时依次停止 Beat、API、Worker；Worker 优先正常退出，强制退出后的记录保留恢复判断。
4. 用候选编排执行 Alembic 和 checkpointer 初始化。首次从含 `scheduler` 的旧布局切换时，再执行一次 `python -m agent_lab.tasks.bootstrap`，把已有文档待办接到必要批次。交接复用业务回收规则，将已停止旧消费者遗留的解析／预览领取重新排队，无需等待计算超时；索引准备、发布及接收未决写入仍按原规则核实。普通部署不运行它，以免重建已取消或失败的任务。若旧部署未用标准服务名，操作者须在核实后显式执行这一次交接；仅执行 Alembic 不会受理这些旧业务待办。
5. 迁移和交接成功后才将候选替换为正式编排，启动 Redis、API、Worker 和单个 Beat。失败保持停止，先检查迁移版本及前提再恢复部署，不自动运行旧进程。该切换包含停机；旧已受理工作保留，错过的周期不补执行。
6. 分别核对 API `/health`、Beat 本地就绪、Worker 消息连接，再在批准范围内从网页受理一次任务并按编号核对结果。旧参数缺失、待核实及部分失败应如实可见，配置删除后仍能查已有执行。

```bash
docker compose exec -T task-beat python -m agent_lab.tasks.status --check
docker compose exec -T task-worker sh -c 'celery -A agent_lab.tasks.celery_app:app inspect ping --destination "worker@$HOSTNAME" --timeout 5'
docker compose run --no-deps --rm backend python -m agent_lab.scheduler_maintenance
```

第三条默认只查看执行者、心跳和占用，输出不含连接串、正文或第三方错误文本。页面的 `needs_attention` 不是允许重新执行的信号。先依据 owner 识别并确认所属进程已经停止，再确认 Qdrant/S3 等远端未决写入已结束；仅确认容器退出或心跳过期还不够。

人工确认完成后，才在新版容器中执行对应恢复命令：

```bash
docker compose run --no-deps --rm backend python -m agent_lab.scheduler_maintenance --run-id <任务执行UUID> --confirm-stopped
# 没有任务执行记录的 CLI 或遗留手动 Pipeline，占用通过 operation-id 定位：
docker compose run --no-deps --rm backend python -m agent_lab.scheduler_maintenance --operation-id <占用UUID> --confirm-stopped
```

恢复会拒绝近期仍有心跳的执行，关闭失联执行记录、解除相关占用并清除其待核实提示；已经失败的结果和统计继续保留。它不重新执行业务，也不删除 Document 或删除待办。删除待办由下一次正常清理重新核实并继续处理，失败不是“下次一定成功”的保证。

日志用 `job_id`、`run_id`、`operation_id`、`owner` 关联：等待写资源、业务失败、客户端关闭失败、终态未保存是不同问题。业务成功但终态未保存时，不能直接再跑一次当作修复。

回退前必须停止所有相关写入口并核实未决写入。公共任务迁移改变了受理与回执契约，拒绝自动 downgrade；不能通过删除执行、回执或业务待办强行降级。若必须回退到旧任务协议，应在确认数据损失范围后恢复切换前的配套备份，而非只回滚代码；已经发生的远端删除不会随数据库恢复自动撤销。

任务不推进时按以下证据区分原因：

| 观察到的现象 | 优先核对 |
| --- | --- |
| 未来周期不再受理，Beat 就绪失败 | Beat 进程、数据库配置与最近推进记录 |
| 已受理但投递错误反复出现 | 详情中的 `dispatch_error_type`、Redis 连接、内存与 AOF 状态 |
| 已投递但长期未领取 | Worker ping、实际 Worker 数量、繁忙任务与配置的队列名 |
| 资源等待 | 等待原因、占用者及其任务进展；正常等待不消耗重试 |
| 待核实 | 原领取、业务完成依据、进程与远端未决写入；不按心跳直接解锁 |
| 业务已完成但终态未确认 | 条件收尾及业务依据；不能再次执行来“补结果” |

Redis 队列长度只反映消息数量，不能代表持久受理或业务健康。自带实例可只读查看 `docker compose exec -T redis redis-cli info memory` 和 `info persistence`，自管实例使用对应运维入口；内存满、AOF 写入失败、磁盘不足及持续投递错误均应由部署监控告警。`TASK_QUEUE_VISIBILITY_TIMEOUT` 不是任务时长上限，长任务重投由 PostgreSQL 领取保护。

### 看日志

```bash
cd /opt/agent-lab
docker compose logs --tail 100 backend      # backend 最近 100 行
docker compose logs --tail 100 task-beat task-worker redis
docker compose logs -f backend              # 跟踪
docker compose ps                           # API、Beat、Worker、Redis；Worker 可有多个实例
```

日志上限 10MB × 3 份（compose 里配的）。Docker 默认不限大小，那会慢慢写满磁盘。

### 每次提问都失败，`agent_internal_error` 500

`agent-lab init-checkpointer` 没跑过，四张 `checkpoint*` 表不存在。这个故障很隐蔽：
服务正常启动、检索正常、`/health` 通过，只有提问失败。手工补一次（幂等）：

```bash
docker compose run --rm backend agent-lab init-checkpointer
```

### 空闲一段时间后头几次提问失败，`agent_checkpointer_connection_lost` 503

这是另一回事：池里的连接被服务端掐了（`idle_session_timeout`、中间代理回收、PG 重启都会
造成），日志里会有 `discarding closed connection`。重发即可，表是好的。

### 容器无限重启，日志只有 `exec format error`

```text
exec /app/.venv/bin/uvicorn: exec format error
```

镜像架构和服务器不符。这台是 Ampere A1（`uname -m` → `aarch64`），镜像必须是
`linux/arm64`。工作流用 `runs-on: ubuntu-24.04-arm` 原生构建，并在构建步骤写死
`platforms: linux/arm64`，两处都不要改回 x64。

这个错的迷惑性在于它长得像「文件坏了」或「路径不对」，但那两种情况报的是
`no such file or directory`。`exec format error` 是 ENOEXEC，只有一个含义：
内核认出这是可执行文件，但看不懂里面的机器码。

确认现有镜像的架构：

```bash
docker image inspect <BACKEND_IMAGE> --format '{{.Architecture}}'   # 应为 arm64
uname -m                                                            # 应为 aarch64
```

### 推镜像失败：`unknown manifest class for application/vnd.oci.empty.v1+json`

ACR 个人版不认 buildx 默认附加的 provenance / SBOM 证明。工作流里已经用
`provenance: false` / `sbom: false` 关掉了，**这两行不是优化，删掉就推不上去**。

这个故障的表现容易误导：所有镜像层和镜像本身都推成功了，只有附加的证明 manifest 被拒，
日志里前面全是正常的 `writing layer`，看起来像网络或权限问题。同理构建缓存用
`type=gha` 而不是 `type=registry`。理由见
[ADR 0008](adr/0008-backend-in-image-frontend-as-static-files.md) 最后一条。

### 部署成功但代码没更新

检查工作流里 `docker compose pull` 是否执行成功。也可能是 `.env` 里的 `BACKEND_IMAGE`
被填成了和 CI 推送地址不同的值。

### 容器起不来

```bash
docker compose logs --tail 50 backend
```

常见原因：

- `.env` 配置非法 → 启动即 `ValidationError`，日志里有字段名。
- 写了 `LLM_CHECKPOINT_POOL_SIZE` → 同上，见「一.3」第 4 点。
- `DATABASE_URL` 写了 `localhost` → 容器里的 `localhost` 是容器自己，连不上。

### `/agent/*` 返回 503 但检索正常

LLM 配置缺失或会话记忆连不上时，Agent Runtime 装配失败是**非致命**的：进程照常启动，
只有 `/agent/*` 不可用。看启动日志里有没有 `Agent 运行时装配失败`。

注意 `LLM_MODEL` 填成上游不存在的模型名是另一种情况：启动完全看不出来，要到第一次提问
才报错。

### 回滚

当前只推 `backend-latest`，不打版本 tag。仅当数据库与消息协议兼容时，才能对旧 commit 重新构建部署；跨共享任务迁移不能直接运行旧代码，按上面的备份恢复边界处理。操作须先确认目标版本和环境授权。

## 四、手动运维命令

```bash
cd /opt/agent-lab

# 手工同步新闻并索引一轮
docker compose run --rm backend agent-lab run-once

# 只同步，不索引
docker compose run --rm backend agent-lab sync-news

# 建恢复账号（网页进不去时才用）
docker compose run --rm backend agent-lab create-user --email recovery@example.com --superuser

# 重启
docker compose restart backend

# 部署连接配置变化后重建以重新注入环境；网页周期修改由 Beat 动态读取
docker compose up -d task-beat task-worker

# 停止（不会被 restart 策略自动拉起）
docker compose stop task-beat backend task-worker
```

`run --rm` 起的是一次性容器，用完即删，不影响正在服务的那个。

## 五、与容器化无关：Cloudflare 与账号管理

### 5.1 Cloudflare 与源站防护

1. Cloudflare DNS 的 A/AAAA 记录指向服务器，并按需要开启 Proxy。
2. Cloudflare SSL/TLS 模式选择 **Full (strict)**，源站安装可信证书（例如 Certbot
   Let's Encrypt）。不要使用 Flexible，否则浏览器到 Cloudflare 与 Cloudflare 到源站
   的协议不一致，且生产 Cookie 不应退回非 HTTPS。
3. 若站点只允许经 Cloudflare 访问，应把源站 80/443 限制到 Cloudflare 官方 IP 段，或
   启用 Authenticated Origin Pulls；仅隐藏源站 IP 不是访问控制。
4. 防火墙只开放 SSH、80 和 443；不要把后端 18000、PostgreSQL 5432、Ollama 11434 或
   Qdrant 端口暴露到公网。

### 5.2 首次登录与日常账号管理

1. 访问 `https://<域名>/login`。
2. 使用 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 登录；服务启动同步会在数据库中创建
   该账号，无需先运行 CLI。
3. 从搜索页顶部的“账号管理”进入 `/admin/users`。
4. 创建普通用户或其他超级用户；启用/停用、授权、重置密码和撤销会话都在页面完成。
5. 普通用户不会看到管理入口；即使手动访问 URL，后端也会返回 403。

页面不会显示、保存或回显任何密码。账号停用、密码重置和主动撤销会话会删除
`access_tokens`，旧浏览器下一次请求立即失效。

### 5.3 环境管理员的轮换与恢复规则

修改 `<DEPLOY_DIR>/.env` 后执行 `docker compose up -d`（env 变化会让 compose 重建容器、
重新注入变量）：

- 修改 `AUTH_ADMIN_PASSWORD`：启动同步 Argon2 Hash；若密码真的变化，撤销该账号所有
  现有会话。新密码可立即登录。
- 修改 `AUTH_ADMIN_EMAIL`：新邮箱被创建/同步为唯一环境管理员；旧邮箱账号保留其密码、
  active 和 superuser 状态，仍可由新管理员在网页管理，不会被删除或自动降权。
- 删除 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 两行：启动释放环境托管标记，但不删
  除、不降级旧账号；它仍按数据库中的普通超级用户规则存在。
- 只删除其中一项：配置校验失败，服务不会以半配置状态启动；请同时恢复两项或同时移除。

推荐的轮换顺序是：先确认新 Secret 已写入并备份，再执行迁移（如版本有变化），最后
`docker compose up -d`，随后检查 `/health`、登录和 `docker compose logs`。不要把密码写进
命令行参数、shell history、截图或工单。

CLI 恢复只在网页无法进入时使用（见「四、手动运维命令」的 `create-user`）；恢复后应尽快
登录网页创建/修复账号，并按需要撤销恢复账号会话。CLI 不用于把所有账号配置塞进 `.env`。
