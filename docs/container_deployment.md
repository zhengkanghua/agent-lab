# 容器化部署：GitHub Actions + 阿里云 ACR + 甲骨文

本文是当前部署方式的操作手册：后端跑在容器里，前端是纯静态文件。取舍理由见
[ADR 0008](adr/0008-backend-in-image-frontend-as-static-files.md)。与容器化无关的
Cloudflare 与账号管理内容收在本文第五节。

```text
浏览器
  → Cloudflare
  → OpenResty（TLS、反代、限流、静态站）        ← 自行配置，本文不提供配置
      ├─ /        → <WEB_ROOT>   dist 静态文件
      └─ /api/*   → 127.0.0.1:18000（去掉 /api 前缀）
                      ↓
                 Swarm 路由网格（入口，绑全接口）
                      ↓
                 agent-lab 栈：backend / task-beat / task-worker 三个服务
                      ↓
                 已有 PostgreSQL / Redis / MinIO
                 （这三个容器被接进同一张自持 overlay agent-lab-net，服务才能靠容器名解析到它们）
                 其余上游（Ollama / Qdrant / 大模型 / FreshRSS）走公网域名，与容器挂哪张网无关
```

前端由 OpenResty 提供静态文件；**后端是一个 Swarm 栈**，三个进程（API、单个 Beat、Linux prefork
Worker）用同一个后端镜像，服务定义在 [`backend/docker-stack.yml`](../backend/docker-stack.yml)。
API 校验权限、持久受理并查询；单个 Beat 推进周期和补投；Worker 完成三类周期任务、文档处理批次和
HTTP Pipeline。PostgreSQL 保存状态和结果，Redis 传递任务消息，后续缓存等用途共用连接并区分键前缀。
文件与 FreshRSS 的文档待办不需要额外 cron，进程职责与恢复决策见
[ADR 0019](adr/0019-scheduled-execution-and-write-coordination.md)。

**部署方式是「先起后停」**：迁移与起新任务都在旧任务还在服务的时候完成，旧任务只在新任务被判定
启动成功之后才被停掉；它手上的在途运行会排空到可交接的边界、交给新进程接着跑完
（见 [ADR 0039](adr/0039-swarm-start-first-deploys.md) 与 [ADR 0040](adr/0040-run-handover-on-deploy.md)）。
**写入者只有 CI**；图形界面（Portainer）只用于查看、看日志、手动重启与应急回滚，不要用它改服务定义。

发布流程：CI 用 `docker stack deploy` 部署，一次性容器（Redis 自检、用量库配置自检、两个迁移、建表初始化）都跑在 `agent-lab-net` 上，运行顺序与几条不可调的顺序约束见下面「二、日常部署」。**网关侧要求**（登录限流、请求体大小、SSE 读超时）见 [`backend/README.md`](../backend/README.md) 的「生产前置要求」一节——服务本身不做这些，需要在 OpenResty 侧落实；Cloudflare 与账号管理在第五节。

## 一、服务器一次性准备

以下步骤只在第一次部署前做一遍。**示例中的路径、用户名和 registry 地址都是占位符，请替换
为实际值**：部署目录以 `/opt/agent-lab` 为例、静态站目录以
`/opt/1panel/www/sites/<站点名>/index` 为例、部署用户以 `deploy` 为例。

本仓库是公开仓库，所以真实的 registry 地址、部署路径都不写在代码和文档里，而是放在
GitHub Variables（CI 侧）和服务器 `.env`（运行侧）。三者的对应关系见第 7 节。

### 1. 建部署用户并加入 docker 组

CI 用这个账号 SSH 进来跑 `docker stack deploy`（以及一次性容器）和接收 dist。不用 root：那台机器上还有其他
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

### 3. 建部署目录，放栈文件与 .env

```bash
sudo mkdir -p /opt/agent-lab
sudo chown deploy:deploy /opt/agent-lab
```

部署目录里放的是栈编排 `docker-stack.yml`（由 CI 上传成 `docker-stack.next.yml`）与 `.env`。全新环境把仓库 `backend/docker-stack.yml` 复制成 `<DEPLOY_DIR>/docker-stack.yml`；从旧的普通容器形态迁过来的环境保留 `docker-compose.yml`——它已经**不是日常部署用的那一份**，而是「退回旧部署方式」的逃生路径（见第三节）。

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

填写时只有四类要点，其余键一律照 [`backend/.env.example`](../backend/.env.example) 的注释与
[后端 README 的环境变量表](../backend/README.md) 填，本文不重复：

1. **地址类值写容器名**（`redis`、`postgresql`、`minio`），不要写 `127.0.0.1`（容器里的
   `localhost` 指容器自己）；`REDIS_URL` 没配时栈校验直接报错，密码单独填 `REDIS_PASSWORD`。
   API、Beat、Worker 的 Redis 配置与 `TASK_QUEUE_NAME` 必须一致。
2. **`LLMOPS_DATABASE_URL` 必填**，而且是**另一个**库（同一个实例上另建 `llmops`，见
   [ADR 0032](adr/0032-usage-data-in-separate-database.md)）；少了它进程起不来，部署工作流在停旧
   之前会先自检这一项。
3. **不要写 `LLM_CHECKPOINT_POOL_SIZE`**：它在 `config/llm.py` 是 `strict=True`，而 `env_file`
   注入的全是字符串，配上就会启动即 `ValidationError`。生产还要有 `AUTH_COOKIE_SECURE=true`。
4. **`WORKER_COUNT` 是 API 进程数、`TASK_WORKER_CONCURRENCY` 是每个 Worker 的子进程数**；要加
   Worker 实例就改编排文件里的 `deploy.replicas`，Beat 始终只有一个实例（详见后端 README 的环境变量表）。

已有的 **Redis 与原件存储**都由各自部署管理：本项目不创建、不修改它们（AOF/everysec、持久盘、
`noeviction`、桶的私有与保留策略都见[后端 README 的生产前置要求](../backend/README.md)），
只**创建并自持一张 overlay**（`agent-lab-net`，建网与接线见第 5 小节）——这张网上只有我们的三个
服务与它们依赖的三个容器，比今天「全机共用面板那张网」更窄。

部署时可先用新镜像做一次离线资源检查（不连接数据库、S3 或模型服务；也**不能代替原件存储验收**
——真实 S3 的读写与删除验收入口见[后端测试说明](../backend/README.md#测试)）：

```bash
docker run --rm --network agent-lab-net --env-file .env "$IMG" python -m agent_lab.prepare_document_resources --check
```

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

### 5. 加入集群、建网并接通三个容器内依赖

后端改成 Swarm 服务之后，宿主机必须先是一个单机集群（一次性动作，**不动任何正在跑的容器**）：

```bash
docker swarm init --default-addr-pool 10.88.0.0/16 --default-addr-pool-mask-length 24
```

> 多网卡时它会提示指定 `--advertise-addr`；`--default-addr-pool` 是为了避开 VCN 的 `10.0.0.0/24`
> 与各 bridge 的 `172.x`（这两个网段已被占用）。撤销这条动作是 `docker swarm leave --force`。

**网络。** 栈服务只能挂 `scope=swarm` 的网络，而面板建的 `1panel-network` 是 `local` 作用域的
bridge（拿去 `docker stack deploy` 会直接报 scope 不对，而且已建好的网改不了），所以本项目自持
一张 overlay，并在 `docker-stack.yml` 里声明成 `external: true`、名字写死：

```bash
docker network create --driver overlay --attachable agent-lab-net
docker network inspect agent-lab-net --format 'driver={{.Driver}} scope={{.Scope}} attachable={{.Attachable}}'
```

**三个容器内依赖必须接进这张网。** 服务靠容器名解析它们（`REDIS_URL`/`DATABASE_URL`/
`LLMOPS_DATABASE_URL`/`S3_ENDPOINT` 都写容器名），不接的话 API 能起来但连不上库、Redis 或 minio。
接线是**叠加**，不动它们原来那张网，也不影响面板上别的应用：

```bash
for c in redis postgresql minio; do
  docker network connect --alias "$c" agent-lab-net "$c"
done
```

**验证。** 仓库里的 `scripts/verify-agent-lab-net.sh` 会在目标网络上起一个一次性容器，真跑一遍：
Redis PING（用应用自己的配置）、两个 PostgreSQL 库各一次 `SELECT 1`、`S3_ENDPOINT` 的解析与 TCP。
把它传到部署目录一次，之后接线复核与「线丢了」的排查都用它：

```bash
bash verify-agent-lab-net.sh                    # 查 agent-lab-net
bash verify-agent-lab-net.sh 1panel-network      # 查面板那张网（老路子应当照旧可用）
```

**断开某根线**（回退用）：`docker network disconnect agent-lab-net <容器名>`。

**「线丢了」的症状与恢复。** 面板重建 `redis` / `postgresql` / `minio` 容器时，新容器默认只插在面板
那张网上，这张 overlay 上的线就断了。症状分别是：

| 丢的是 | 症状 |
|---|---|
| `redis` | 容器健康、页面照开，但队列与缓存静默停摆（任务不再受理/投递） |
| `postgresql` | 接口大量失败，甚至进程起不来（生命周期里同步环境管理员那一步会抛错） |
| `minio` | 原件上传与读取失败 |

恢复就是重新接一次（上面那段 `for c in ...`），接完用验证脚本确认。部署工作流在停旧进程之前会跑
一次 Redis 连接自检（它跑在同一张网上），所以「线丢了」会挡住部署，而不是静默上线。

### 6. 确认端口未被占用

默认后端映射到宿主机 `18000`。确认它是空的：

```bash
ss -tlnp | grep :18000
```

有输出说明被占用，改 `<DEPLOY_DIR>/.env` 里的 `BACKEND_PORT`，并同步改 OpenResty 的
反代目标。

### 7. 配置 OpenResty

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

### 8. 配置 GitHub Secrets 与 Variables

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

地址放 Variables 而不是 Secrets，是因为 Secrets 在日志里会被打码（`docker push ***/***:…`），
调 CI 时几乎无法定位；而这些值本来也不是凭据。工作流会在第一步就校验这五个 Variables 非空，
缺了立刻失败并指出缺哪个。

### 更换 registry 时必须同步改的地方

镜像地址现在存在**三个互相看不见的地方**，没有任何自动比对。改 registry、命名空间或仓库名时
必须三处一起改，漏一处的表现是「CI 全绿、容器也重启了，但跑的还是上一版代码」——没有报错。

| 位置 | 改什么 |
|---|---|
| GitHub Variables | `ACR_REGISTRY`、`ACR_NAMESPACE`、`ACR_REPOSITORY` |
| 服务器 `<DEPLOY_DIR>/.env` | `BACKEND_IMAGE` 整行 |
| 服务器 `docker login` | 重新登录新 registry（凭据存在 `~/.docker/config.json`） |

改完先手工验证一次再推代码（在部署目录执行）：
`docker pull "$(grep -E '^BACKEND_IMAGE=' .env | cut -d= -f2-)"`——拉得动再推。

### 9. 首次部署前先手工验证一遍

不要指望第一次就让 CI 跑通。手工跑一遍的顺序、命令与注意事项都在**第二节「手工跑一遍同一套流程」**，
本手册不再重复一遍；那里也写了初次部署才碰得到的两条前提：ACR 里还没有镜像时第一次 `pull` 会失败
（先让 CI 跑一次把镜像推上去），以及业务库必须已存在（本文不涵盖它的创建）。

## 二、日常部署

推代码到 `main` 即自动部署。也可以在 GitHub 的 Actions 页面手动触发
（`workflow_dispatch`），用于「服务器侧改了配置想重跑一次」而不必造空提交。

**工作流只做构建与部署，不跑测试。** 测试与回归在本地完成（命令见
[后端 README](../backend/README.md#测试) 和 [前端 README](../frontend/README.md#验证)），
推送后 CI 从构建开始。唯一保留的构建期检查是前端 `npm run build` 内含的 `vue-tsc -b`。

### 配置放在哪儿（改之前先看这张表）

只有两处：

| 想改的东西 | 改哪里 | 怎么生效 |
|---|---|---|
| 更新顺序、停止宽限、副本数、健康检查、端口发布、挂哪张网、各服务的 command、日志上限 | git 里的 `backend/docker-stack.yml` | 推一次（CI 部署） |
| 环境变量（`DATABASE_URL`/`REDIS_URL`/`LLMOPS_*`/`S3_*`/`AUTH_*`/`LLM_*` 等）、镜像地址 `BACKEND_IMAGE`、端口号的值 | 服务器 `<DEPLOY_DIR>/.env`（1Panel 文件管理器或 `vi`） | 改完**重新部署一次**才生效（`.env` 的值是部署时读进服务定义的）；最省事是在 GitHub 的 Actions 页手动 Run workflow |
| 任务的周期、参数、策略 | 网页的任务管理（存在数据库） | Beat 每次动态读取，不用重新部署 |

两个容易混的点：**镜像摘要（`@sha256:…`）不在任何文件里**——它是部署时去 registry 解析后钉进服务定义的，回滚（`--rollback`）就靠它；**`.env` 里只有 6 个键会被栈文件引用**（`BACKEND_IMAGE`、`BACKEND_PORT`、`TZ`、`REDIS_URL`、`WORKER_COUNT`、`TASK_WORKER_CONCURRENCY`），部署前由工作流导出成进程环境，其余四十来个键通过 `env_file` 直接注入容器；若在栈文件里新增一个 `${}` 引用，要同步往工作流里那个 6 键列表加一行。

CI 的完整顺序在 [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) 里。它做的事是：
**校验候选栈文件 → 在项目网络上跑一次性容器（Redis 自检、用量库配置自检、业务库迁移、建用量库与
其迁移、建表初始化）→ 停掉还在按旧方式跑的普通容器（只有第一次切换时有活干）→
`docker stack deploy`（先起后停在这里面）→ 等新任务就绪 → 清理旧镜像与已退出的任务容器**。
下面这些约束是有意的，不要调整：

1. **迁移都在起新任务之前**，且 `alembic upgrade head` 在 `agent-lab init-checkpointer`
   之前。理由见 [ADR 0004](adr/0004-checkpointer-tables-outside-alembic.md)。关键在于这一整段
   **发生在旧版本还在服务的时候**——这正是「先起后停」与旧流程的区别（旧流程是「停旧 → 迁移 → 起新」，
   中间那段端口上没人服务）。
2. **后端部署在前端上传之前**：迁移失败时部署中止，前端仍是旧版本，不会出现「新前端调
   老后端」。
3. **`docker stack deploy` 必须带 `--with-registry-auth`**：它让 CLI 去 registry 解析摘要、把 digest 写进服务定义，于是「回滚到上一版」回到的是那一版**具体镜像**，而不是一个已经被覆盖的可变标签；同时也在告诉节点用哪个凭据去拉私有镜像。
4. **栈文件里的 `${}` 引用必须在进程环境里**：`docker stack deploy` 与 `docker stack config` 都**不读 `.env`**（只有 `docker compose` 读），所以部署前要把被引用的那几个键从 `.env` 导出——键名与可执行的那段循环见下面「手工跑一遍同一套流程」第 2 步。其余键由服务定义里的 `env_file` 注入容器，不经过环境；**不能整份 `source .env`**（里面 `LLM_USER_AGENT` 这类值带空格与括号，会被 shell 拆坏）。在栈文件里新增一个 `${}` 引用时，记得同步工作流里那个列表；写成 `${VAR:?}` 的键缺值会在校验那一步直接报错。
5. **停旧任务由更新器按「新任务是否启动成功」判定**，`failure_action: rollback` 管的是「容器退出」那一类（起来了但不健康的，靠就绪端点被看见）。API 的停止宽限 180 秒 = uvicorn 关停超时 30 秒（`entrypoint.sh`）＋ 排空上限 120 秒（`agent/limits.RUN_DRAIN_TIMEOUT_SECONDS`）＋ 收尾写入的余量：旧进程在收尾里把在途运行排空到可交接的 superstep 边界、在会话行上写下「等接手」标记再退出，新 API 起来后扫到标记接手续跑（见 [ADR 0040](adr/0040-run-handover-on-deploy.md)）；Beat 30 秒（它必须「先停后起」，否则两个调度器同时活着会把周期任务投两次）、Worker 360 秒（让手上的文档批次做完）。只有**第一次切换**时工作流会去停旧的普通容器（Swarm 要发布同一个宿主机端口），之后那一步是空操作。
6. **用量库配置自检与 Redis 连接自检都排在起新任务之前**：`LLMOPS_DATABASE_URL` 缺失或不合法时应用启动就会失败，把生产停在一半才发现 `.env` 少了一项是完全可以避开的。
7. **迁移阶段有两条链**：业务库的 `alembic upgrade head`，以及用量库的建库 + `alembic -c alembic_usage.ini upgrade head`。两者都在 `docker stack deploy` 之前完成，任一条失败即中止部署（此时旧版本仍在服务）。
8. **删列、改列名、改语义、拆约束的迁移拆两次发布**：这次只发「代码不再用它」，下一次发布才真删。原因见第三节「哪种迁移会让回滚退路消失」。
9. **等就绪看两条**：更新器自己的结论（`UpdateStatus.State`）与任务本身（运行中任务数等于副本数、API 容器健康）。只看「任务在跑且健康」不够——回滚之后跑的正是旧任务，它当然健康。任务更新进来之后，旧任务容器会被停掉但**留在节点上**（`Exited`），所以工作流在就绪之后会清一次本栈已退出的任务容器；手工跑同一套流程时也要清：`docker ps -a --filter name=agent-lab_ --filter status=exited --format '{{.Names}}' | xargs -r docker rm`。

> **一条与常见说法相反的实测（2026-10-01，本机 Docker 29）。** **未就绪的任务收不到流量**：带健康检查的服务里，新任务在就绪之前那 980 次请求 0 失败、最慢 3ms（对照实验：把健康检查去掉后，窗口里的请求会排上十几秒）；而 `/ready` 一直返 503 的那个任务，入口一次请求都没转运给它（拿到的转发请求数是 **0**）。也就是说这套编排里「就绪之后才切流量」是成立的，不必自己再写一个代理。**升级 Docker 大版本后请复核这一条**——它决定我们还要不要加一个「自己拿指针」的组件。

### 手工跑一遍同一套流程

CI 不在手边（或想先验证服务器侧）时，照下面这套顺序手工执行；它与工作流做的事一一对应。
`<DEPLOY_DIR>` 就是仓库 Variables 里的那个路径，命令都在它下面执行：

```bash
cd <DEPLOY_DIR>

# 1) 登录并拉新镜像（目录里已经上传了本次的候选文件 docker-stack.next.yml）
#    两条前提：ACR 里还没有镜像时第一次 pull 会失败（先让 CI 跑一次）；业务库必须已存在（本文不涵盖它的创建）
echo "$ACR_PASSWORD" | docker login "$ACR_REGISTRY" -u "$ACR_USERNAME" --password-stdin
docker pull "$BACKEND_IMAGE"

# 2) 把会被插值的键导出成进程环境（stack deploy 不读 .env）
for key in BACKEND_IMAGE BACKEND_PORT TZ REDIS_URL WORKER_COUNT TASK_WORKER_CONCURRENCY; do
  value="$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  [ -n "$value" ] && export "$key=$value"
done

# 3) 校验候选栈文件（语法、未知字段、${VAR:?} 缺值都在这里报）
docker stack config -c docker-stack.next.yml >/dev/null

# 4) 一次性容器（全跑在项目网络上，且都排在起新任务之前）。四条命令的完整程序在
#    .github/workflows/deploy.yml 的「部署后端」那一步里，照拄即可；形式是：
docker run --rm --network agent-lab-net --env-file .env "$BACKEND_IMAGE" alembic upgrade head
docker run --rm -i --network agent-lab-net --env-file .env --entrypoint python "$BACKEND_IMAGE" - <<'PY'
...（Redis 自检 / 用量库配置自检 / 建用量库：从工作流照拄）
PY
docker run --rm --network agent-lab-net --env-file .env "$BACKEND_IMAGE" agent-lab init-checkpointer

# 5) 如果还有按旧方式跑的普通容器，先停掉（Swarm 要发布同一个宿主机端口）。
#    首次切换、以及「退回旧方式之后再切回」时都需要；平常的部署这一步是空操作。
docker compose -f docker-compose.yml ps -q | grep -q . && {
  docker compose -f docker-compose.yml stop --timeout 180 backend
  docker compose -f docker-compose.yml stop --timeout 360 task-worker task-beat
}

# 6) 起栈（--with-registry-auth 不是可选项；--detach=false 等更新器给结论）
docker stack deploy --with-registry-auth --detach=false -c docker-stack.next.yml agent-lab

# 7) 等就绪与核对
docker service ls --filter name=agent-lab
docker service inspect agent-lab_backend --format '{{json .UpdateStatus}}'      # 应为 completed
docker ps --filter name=agent-lab_backend --format '{{.Names}}\t{{.Status}}'    # 应为 healthy
curl -s -o /dev/null -w '/ready=%{http_code}\n' http://127.0.0.1:18000/ready

# 8) 清掉本栈已退出的任务容器
docker ps -a --filter name=agent-lab_ --filter status=exited --format '{{.Names}}' | xargs -r docker rm
```

> 手工跑时 `docker run` 的 `-i` 不能省：程序是通过 heredoc 送进去的，少了它 CLI 不会把 stdin 交给
> 容器，容器里的 `python -` 会读到空程序、立刻退出 0 而什么都不打印——自检看上去“通过”了其实没跑。

推送完成后执行 `gh run list --limit 1` 核对最新部署，失败时用 `gh run view <run-id>` 查明原因；
修复在本地验证后再推送。Actions 就绪检查覆盖 API、Beat 和 Worker 消息连接，业务验收仍需受控操作与执行编号。

## 三、排查

### 应急速查（线上出问题先看这里）

**第一步：先判断是哪一类，然后只用对应那一条。**

| 现象 | 做什么 |
|---|---|
| 站点/接口大面积报错，怀疑是新版本 | **回上一版**（下面第 1 条）——秒级，不用构建 |
| 某个服务卡住（例如回答一直不返回） | **重启那个服务**（第 3 条） |
| 队列不动、上传失败、库连不上 | 查那张 overlay 的三根线：`bash verify-agent-lab-net.sh`；症状对照见第一节第 5 小节 |
| 分不清 | 先看现状（第 4 条） |

**四条命令（都在部署目录下执行）**

```bash
# 1) 回到上一版：三个服务各回滚一次，然后比对摘要确认真的换了版本
for s in backend task-beat task-worker; do docker service update --rollback "agent-lab_$s"; done
docker service inspect agent-lab_backend --format '{{json .Spec.TaskTemplate.ContainerSpec.Image}}'

# 2) 切到某个指定版本（先列出本地可回滚的版本，再拿完整地址去切）
docker images --filter dangling=true --format '{{.ID}} {{.CreatedSince}}'
docker service update --image <那一版的完整地址，含 @sha256> agent-lab_backend

# 3) 重启某个服务（是「服务」，不是「容器」）
docker service update --force agent-lab_backend

# 4) 看现状与有没有「多出来的容器」
docker service ls --filter name=agent-lab
docker ps --filter name=agent-lab_ --format '{{.Names}}\t{{.Status}}'
```

**在面板里操作的三条规矩（Portainer）**

1. **在「Services」（服务）或「Stacks」（堆栈）里操作**：重启、回滚、改定义都在这一层做——服务层的动作不会留下「活着但没人管」的容器。
2. **「Containers」（容器）里只读**：看日志、看状态可以；**别点它的重启/停止**——那会让编排器另建新任务，而这个容器又自己活过来，变成管理不到的「编外容器」（对 Beat 而言就是两个调度器同时跑）。
3. **别在面板里新建一个栈来「部署」**：那会多出第二份定义，和现有服务抢端口。部署走 CI，或第二节的手工清单。

**哪些垃圾会自动清、哪些要自己清**

| 来源 | 会不会自动清 |
|---|---|
| CI 部署替换下来的旧容器 | ✅ 工作流在就绪后自动清 |
| 服务层操作（面板或 CLI 的 update / rollback / scale）换下来的旧容器 | ❌ 会以「已停止」留在机器上（无害），顺手清一次：`docker ps -a --filter name=agent-lab_ --filter status=exited --format '{{.Names}}' \| xargs -r docker rm` |
| **容器层点重启/停止留下的「编外容器」** | ❌ 必须自己删（它还在跑）：`docker rm -f <名字>` |
| 面板里新建的第二个栈 | ❌ 在 Stacks 里 Stop + Delete |

### 索引重建与发布恢复

Docling 结构化处理与 v3 索引规格的切换已于 2026-09-11 在开发资料上完成：清空后重新导入，不做旧正文迁移，当时的步骤与验收记录在提交 `5c96cfe` 前后的历史里。现在的资料只有一种形态，日常重建只复用当前已采用版本的冻结 Chunk。需要改变正文、章节或切分规则时，应生成候选并重新采用。
重建新 generation 失败时保留原 Alias；发布结果不确定时先按下一节核实旧执行和写占用，再恢复发布：

```bash
docker run --rm --network agent-lab-net --env-file .env "$IMG" agent-lab recover-index-rebuild --generation <目标代次>
```

该命令核对已发布或仍在原 Alias 的状态，不重新向量化。解析或采用失败在文档管理中重试、修正或拒绝；
拒绝只停止使用，明确删除才清除原件和文档历史。原件、向量删除中断保留待办，需继续核对并完成清理。

### 任务执行的排查与恢复

部署后分别核对 API `/health`、Beat 本地就绪、Worker 消息连接，再在批准范围内从网页受理一次任务并按编号核对结果：

```bash
docker exec "$(docker ps -q -f name=agent-lab_task-beat | head -1)" python -m agent_lab.tasks.status --check
docker exec "$(docker ps -q -f name=agent-lab_task-worker | head -1)" sh -c 'celery -A agent_lab.tasks.celery_app:app inspect ping --destination "worker@$HOSTNAME" --timeout 5'
docker run --rm --network agent-lab-net --env-file .env "$IMG" python -m agent_lab.scheduler_maintenance
```

任务不推进时按证据区分原因：

| 观察到的现象 | 优先核对 |
| --- | --- |
| 未来周期不再受理，Beat 就绪失败 | Beat 进程、数据库配置与最近推进记录 |
| 已受理但投递错误反复出现 | 详情中的 `dispatch_error_type`、Redis 连接、内存与 AOF 状态 |
| 已投递但长期未领取 | Worker ping、实际 Worker 数量、繁忙任务与配置的队列名 |
| 资源等待 | 等待原因、占用者及其任务进展；正常等待不消耗重试 |
| 待核实 | 原领取、业务完成依据、进程与远端未决写入；不按心跳直接解锁 |
| 业务已完成但终态未确认 | 条件收尾及业务依据；不能再次执行来「补结果」 |

**恢复前必须先确认旧进程与远端未决写入都已停止**（只看容器退出或心跳过期不够；页面上的
`needs_attention` 不是重跑信号），确认后才执行对应命令；恢复会关闭失联执行记录、解除占用并清除待核实提示，
**不会重新执行业务**，也不删 Document 与删除待办。切换前受理、没有参数快照的历史执行不能人工重试。

```bash
docker run --rm --network agent-lab-net --env-file .env "$IMG" python -m agent_lab.scheduler_maintenance --run-id <任务执行UUID> --confirm-stopped
docker run --rm --network agent-lab-net --env-file .env "$IMG" python -m agent_lab.scheduler_maintenance --operation-id <占用UUID> --confirm-stopped   # 无执行记录的 CLI/遗留 Pipeline
```

回退前要停掉所有相关写入口并核实未决写入：共享任务表的迁移拒绝自动 downgrade，要回到迁移前的版本只能按
备份恢复，而已经发生的远端删除不会随数据库恢复自动撤销。日志用 `run_id`、`operation_id`、`owner` 关联；
队列长度只反映消息数量、不代表业务健康，`TASK_QUEUE_VISIBILITY_TIMEOUT` 也不是任务时长上限。跨存储失败
与心跳语义的完整说明见 [`docs/flows/scheduled-job-execution.md`](../flows/scheduled-job-execution.md)。

### 看日志

```bash
cd /opt/agent-lab
docker service logs --tail 100 agent-lab_backend      # backend 最近 100 行
docker service logs --tail 100 agent-lab_task-beat agent-lab_task-worker
docker service logs -f agent-lab_backend              # 跟踪
docker service ls --filter name=agent-lab    # API、Beat、Worker 各 1/1
```

日志上限 10MB × 3 份（compose 里配的）。Docker 默认不限大小，那会慢慢写满磁盘。

### 某个 API 进程卡住（回答偶尔一直不返回）

**表现**：一个容器里跑着 `WORKER_COUNT` 个 API 进程（默认 2），其中**一个**卡住时请求会被内核随机分
给它——于是症状是「同一个问题有时秒回、有时一直不返回」，而容器本身仍然 `(healthy)`。原因见下一条。

**就绪探针在一个容器两个进程时只是抽样。** 探针每次是一条新连接、内核在两个进程之间分派，所以它只
代表**被问到的那一个**进程；两个里坏了一个时，容器仍可能报就绪。这是「一个容器两个进程」今天就有
的事实，由拓扑决定、不由本次改造引入。

**已记为待办（单独做）：改成「一个容器一个进程 + 多开副本」。** 做法是编排文件里 API 的 `WORKER_COUNT`
取 1（或删掉，entrypoint 默认就是 1）、`deploy.replicas: 2`，并相应改部署工作流的就绪判定（现在写的是
「运行中任务 ≥3」，要按副本数算），然后真部署验证一次、改相关文档。收益：**每个进程都被自己的探针
覆盖**，一个进程卡住时只有那个容器被判不健康、由 Swarm 自己换掉，另一个继续服务——不必再人工重启整个
服务。与 [ADR 0039](adr/0039-swarm-start-first-deploys.md) 末尾那条「已知边界」是同一件事；2026-10-01
决定单独做，不随这次部署改造。

另一个表征：那个进程的日志不再前进（`docker logs` 里一段沉默），而另一个进程照旧收发。

**恢复**：重启这个**服务**（不要重启任务容器，理由见下段）：

```bash
docker service update --force agent-lab_backend
```

重启后容器里的所有进程都是新的，卡住的那个随之消失。**恢复前先抓证据**（两个进程共享日志流，重启
会把现场冲掉）：

```bash
docker ps --filter name=agent-lab_backend --format '{{.Names}} {{.Status}}'
docker logs --tail 100 "$(docker ps -q -f name=agent-lab_backend | head -1)"
docker service ps agent-lab_backend --no-trunc
```

**别用容器层的重启。** 对 Swarm 的**任务容器**做 `docker restart`（或面板里的「重启」按钮），Swarm
会把它判成异常并另建新任务，而旧容器因为自带 `restart: any` 策略继续活着、且不再归编排管。症状是
「服务层看着干净（`docker service ps` 只有一行 Running、`REPLICAS 1/1`），但节点上多出一个同名容器
在跑」；对 Beat 而言就是两个调度器同时活着（周期任务可能被投两次）。手工清理：
`docker rm -f <孤儿容器名>`。

### 对话里的检索工具报错（`qdrant_response_invalid`）

现象：日志里反复出现

```text
WARNING agent_lab.agent.middleware Agent 工具调用失败 tool=search_documents error_type=QdrantSearchResponseError code=qdrant_response_invalid
```

用户侧表现为模型查不到资料、很快就给一个泛泛的回答（工具一调就失败，重试几次后收尾），而**检索页本身
正常**——失败在 Agent 调 Qdrant 那一步的响应校验上，与部署方式无关、也不是网络问题。

**这是改造前就存在的缺陷（2026-10-01 实测确认：改造成 Swarm 之前的容器日志里同样有），待单独一轮处理**，
不在本次部署改造范围内；排查时不要先怀疑部署。

### 每次提问都失败，`agent_internal_error` 500

`agent-lab init-checkpointer` 没跑过，四张 `checkpoint*` 表不存在。这个故障很隐蔽：
服务正常启动、检索正常、`/health` 通过，只有提问失败。手工补一次（幂等）：

```bash
docker run --rm --network agent-lab-net --env-file .env "$IMG" agent-lab init-checkpointer
```

### 空闲一段时间后头几次提问失败，`agent_checkpointer_connection_lost` 503

这是另一回事：池里的连接被服务端掐了（`idle_session_timeout`、中间代理回收、PG 重启都会
造成），日志里会有 `discarding closed connection`。重发即可，表是好的。

### 容器无限重启，日志只有 `exec format error`

```text
exec /app/.venv/bin/uvicorn: exec format error
```

镜像架构和服务器不符。这台是 Ampere A1（`uname -m` → `aarch64`），镜像必须是 `linux/arm64`；
工作流用 `runs-on: ubuntu-24.04-arm` 原生构建并写死 `platforms: linux/arm64`，两处都别改回 x64。
（`exec format error` 是 ENOEXEC——内核认出了可执行文件但看不懂里面的机器码，不是「文件坏了」也不是
路径问题，那两种报的是 `no such file or directory`。）确认现有镜像的架构：

```bash
docker image inspect <BACKEND_IMAGE> --format '{{.Architecture}}'   # 应为 arm64
uname -m                                                            # 应为 aarch64
```

同一原因还带来前端的一个约束：`npm ci` 也跑在 ARM 上，依赖 `package-lock.json` 里的 ARM 原生包
（`@rolldown/binding-linux-arm64-gnu`、`lightningcss-linux-arm64-gnu`）——**换 Vite/Tailwind 大版本后
要复查一次**，包名或平台标签变了而 lock 没跟上，`npm ci` 会在 CI 里直接失败。

### 推镜像失败：`unknown manifest class for application/vnd.oci.empty.v1+json`

ACR 个人版不认 buildx 默认附加的 provenance / SBOM 证明。表现容易误导：**所有镜像层都推成功了，只有
附加的证明 manifest 被拒**，日志前面全是正常的 `writing layer`，看起来像网络或权限问题。工作流里的
`provenance: false` / `sbom: false` **不是优化，删掉就推不上去**；缓存用 `type=gha` 而不是
`type=registry` 同理。理由都写在 `.github/workflows/deploy.yml` 的 build 步骤注释里。

### 部署成功但代码没更新

工作流会在部署早期比对 CI 拼出来的镜像地址与服务器 `.env` 里的 `BACKEND_IMAGE`，不一致就直接失败
（这两处怎么同步改见「更换 registry」那节）；如果服务定义里的摘要根本没变，说明 ACR 里那一版
没更新——回去看构建步骤是否真的推成功了。也可能是 `.env` 里的 `BACKEND_IMAGE`
被填成了和 CI 推送地址不同的值。

### 容器起不来

```bash
docker service logs --tail 50 agent-lab_backend
```

常见原因：

- `.env` 配置非法 → 启动即 `ValidationError`，日志里有字段名。
- 写了 `LLM_CHECKPOINT_POOL_SIZE` → 同上，见「一.3」第 4 点。
- `DATABASE_URL` 写了 `localhost` → 容器里的 `localhost` 是容器自己，连不上。

### `/agent/*` 返回 503 但检索正常

LLM 配置缺失或会话记忆连不上时，Agent Runtime 装配失败是**非致命**的：进程照常启动，
只有 `/agent/*` 不可用。看启动日志里有没有 `Agent 运行时装配失败`。

`LLM_MODEL` 填成上游不存在的模型名，启动期就会暴露：进程会向上游拉一次模型列表比对，不在
其中就按同样的方式关掉 `/agent/*`。只有列表拉不到（网络不通、接口不支持）时才只记 warning，
要等到第一次提问才报错。

### 回滚（切回上一版镜像）

部署把**镜像摘要**钉在服务定义里（`...backend-latest@sha256:…`），所以「切回上一版」是一条命令、回到的是那一版具体镜像，而不是一个已经被覆盖的可变标签：

```bash
cd <DEPLOY_DIR>
for s in backend task-beat task-worker; do docker service update --rollback "agent-lab_$s"; done
docker service ls --filter name=agent-lab
docker service inspect agent-lab_backend --format '{{json .Spec.TaskTemplate.ContainerSpec.Image}}'
```

它走的是每个服务自己的 `PreviousSpec`（上一次部署那份定义）。**核对方法**：逐个服务比对回滚前后的
镜像引用（含摘要）**必须不同**——只比整体差异会掩盖个别服务没换版本，而相同就说明命令成功也只是假绿灯。

**它只回代码，不回数据库。** 能成立要同时满足三件：

1. **上一版镜像还在**：部署后它会作为无名镜像留在本地（镜像清理只删 7 天前的无名镜像，就是为了保住它）；
   即使本地没有了，也能按摘要从 ACR 拉回——摘要不可变。
2. **这次迁移对旧版本安全**：见下一条。
3. **图状态结构没变**：与接手（排空续跑）同一条前提——旧代码接不了新形状的 checkpoint。

### 哪种迁移会让回滚退路消失

**删列、改列名、改语义、拆约束的迁移，必须拆两次发布**：这次只发「代码不再用它」，下一次发布才真删。
原因是任何回滚（`--rollback`，或重新部署旧版本）都要求旧代码能跑在当前结构上：结构先变、代码后退，
旧代码会直接报「列不存在」，或者更糟——不报错但留下脏数据（当年拆外键约束那次就是这一类）。

做过这类破坏性迁移之后，**回滚退路就不存在了**，只能按「恢复备份」处理；也不能指望 downgrade 一把梭
（共享任务表的迁移拒绝自动 downgrade）。这条与用不用 Swarm 无关，只是 Swarm 把「秒级回滚」变成一个
真实承诺，所以必须一起守；本次不引入机械门禁，靠这条规则与评审。

### 退回旧部署方式（逃生路径）

改造期间与改造之后，服务器上同时留着旧的普通容器编排（`docker-compose.yml` 与同一份 `.env`，它引用的
`1panel-network` 也还在）。新栈出问题时按顺序执行下面三步就能退回旧方式：

```bash
cd <DEPLOY_DIR>
docker stack rm agent-lab                                                   # 1) 撤掉三个 Swarm 服务（端口随之释放）
sleep 20
docker compose -f docker-compose.yml up -d backend task-worker task-beat    # 2) 按旧编排起普通容器
docker ps --filter name=agent-lab --format '{{.Names}}\t{{.Status}}'         # 3) 三个都 Up
```

退回后写入口变成普通容器（1Panel 会重新管到它们）；确认可用之后再按第二节的流程切回 Swarm。
**这条路径尚未演练过**（验收 6 被跳过了，2026-10-01 决定）：它的每条命令都是改造前长期在用的，很可能直接
能用，但第一次真用它时请逐步核实三件事——`docker stack rm` 后端口是否真的释放（`sleep 20` 之后
`ss -tlnp | grep 18000`）、旧容器是否三个都起来（`docker ps`）、以及它们的日志里有没有连不上依赖的报错。

## 四、手动运维命令

三条只在这里出现的容器内命令（都挂 `agent-lab-net`，因为要够到 Redis/PostgreSQL/minio；`docker run --rm` 用完即删，不影响正在服务的那个）：

```bash
cd <DEPLOY_DIR>
IMG=$(grep -E '^BACKEND_IMAGE=' .env | head -1 | cut -d= -f2-)

# 手工同步新闻并索引一轮
docker run --rm --network agent-lab-net --env-file .env "$IMG" agent-lab run-once

# 只同步，不索引
docker run --rm --network agent-lab-net --env-file .env "$IMG" agent-lab sync-news

# 建恢复账号（网页进不去时才用）
docker run --rm --network agent-lab-net --env-file .env "$IMG" agent-lab create-user --email recovery@example.com --superuser
```

看日志与状态、重启某个服务、起停整栈、清已退出/孤儿容器，都在第三节（「应急速查」「看日志」）与第二节里，这里不重复。

### 图形界面（Portainer）能做什么、不能做什么

**四个词是同一个东西的四层**：**堆栈**（一份打包定义：`docker-stack.yml` + 栈名，我们只有一个）→
**服务**（期望状态：镜像、副本、更新顺序、宽限、健康检查；我们有三个，**部署改的就是它**，蓝绿也发生在
这一层）→ **任务**（服务建的一次执行单元，更新时会短暂看到新旧两个）→ **容器**（节点上真正跑的那个，
`agent-lab_backend.1.<后缀>`）。**平时看服务层与容器日志、应急在服务层操作、容器层只读。**

**定位：查看、看日志、应急重启/回滚；写入者始终是 CI。** 堆栈显示「在 Portainer 外部创建、控制权受限」
是**预期的**（它由 CI 用 `docker stack deploy` 建），所以别指望用它重新部署；**也不要新建一个栈来「部署」**
——那会出现第二份服务定义，症状是「改了没生效」。回滚以 CLI 为准（第三节）。

**唯一的坑：对「容器」点重启/停止会留下孤儿容器。** Swarm 把那个任务判成异常、另建新任务，而旧容器
因为自带 `restart: any` 继续活着且不再归编排管（对 Beat 就是两个调度器同时跑）。**重启要在服务层做**：
`docker service update --force <服务名>`，产生的孤儿用 `docker rm -f <容器名>` 清掉；手工做过服务层操作后，
顺手清一次它换下来的旧容器（命令见第三节应急速查的垃圾清理表）。

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
3. 从侧栏底部的“后台管理”进入 `/admin`，账号管理是后台的一个分区（`/admin/users`）。
4. 创建普通用户或其他超级用户；启用/停用、授权、重置密码和撤销会话都在页面完成。
5. 普通用户不会看到管理入口；即使手动访问 URL，后端也会返回 403。

页面不会显示、保存或回显任何密码。账号停用、密码重置和主动撤销会话会删除
`access_tokens`，旧浏览器下一次请求立即失效。

### 5.3 恢复用超级用户的轮换与恢复规则

修改 `<DEPLOY_DIR>/.env` 后**按第二节「手工跑一遍同一套流程」重新部署一次**（`docker stack deploy`
会把 `.env` 的新值注入服务定义并滚动更新，服务会自动重建）：

- 修改 `AUTH_ADMIN_PASSWORD`：启动同步 Argon2 Hash；若密码真的变化，撤销该账号所有
  现有会话。新密码可立即登录。
- 修改 `AUTH_ADMIN_EMAIL`：新邮箱被创建/同步为唯一带环境托管标记的超级用户；旧邮箱账号保留其密码、
  active 和 superuser 状态，仍可由新管理员在网页管理，不会被注销、也不会被自动改动角色。
- 删除 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 两行：启动释放环境托管标记，但不注
  销、也不改动它的角色；它仍按数据库中的普通超级用户规则存在。
- 只删除其中一项：配置校验失败，服务不会以半配置状态启动；请同时恢复两项或同时移除。
- 配置的邮箱在库里是一个**已注销**的账号：启动直接失败并说明需要人工处理，不会把这个账号
  拉回成能登录的超管。这一行是注销的终态，启动同步不能撤销它；要恢复入口就换一个邮箱，
  或者人工处理库里那一行（第一版没有恢复入口）。

推荐的轮换顺序是：先确认新 Secret 已写入并备份，再执行迁移（如版本有变化），最后
上面那套重新部署，随后检查 `/health`、登录和 `docker service logs --tail 50 agent-lab_backend`。不要把密码写进
命令行参数、shell history、截图或工单。

CLI 恢复只在网页无法进入时使用（见「四、手动运维命令」的 `create-user`）；恢复后应尽快
登录网页创建/修复账号，并按需要撤销恢复账号会话。CLI 不用于把所有账号配置塞进 `.env`。
