# 容器化部署：GitHub Actions + 阿里云 ACR + 甲骨文

本文是当前部署方式的操作手册：后端跑在容器里，前端是纯静态文件。取舍理由见
[ADR 0008](adr/0008-backend-in-image-frontend-as-static-files.md)。

上一代部署方式（宿主机 uv + systemd + Nginx 发 dist）记录在
[`vps_deployment.md`](vps_deployment.md)。**那份文档没有废弃**：它有几节内容与部署方式
无关，本文不复制、只引用，见下面「本文不重复的内容」。

```text
浏览器
  → Cloudflare
  → OpenResty（TLS、反代、限流、静态站）        ← 自行配置，本文不提供配置
      ├─ /        → <WEB_ROOT>   dist 静态文件
      └─ /api/*   → 127.0.0.1:18000（去掉 /api 前缀）        后端容器
                      → 远程 PostgreSQL / Ollama / Qdrant / FreshRSS
```

前端没有容器：`dist` 是静态文件，不运行、无依赖、无需隔离。后端一个容器。

## 本文不重复的内容

以下几节与「进程怎么跑起来」无关，容器化没有改变它们。请直接读
[`vps_deployment.md`](vps_deployment.md) 对应小节，本文不复制，以免两处说法不一致：

| 内容 | 位置 |
|---|---|
| Cloudflare DNS / Proxy / SSL 模式（必须 Full strict，不能用 Flexible） | `vps_deployment.md` 第 1 节 |
| 首次登录与日常账号管理 | `vps_deployment.md` 第 7 节 |
| `AUTH_ADMIN_*` 轮换规则、CLI 恢复入口 | `vps_deployment.md` 第 8 节 |

网关侧要求（登录限流、请求体大小、SSE 读超时）见
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

把仓库里的 `backend/docker-compose.yml` 复制到 `<DEPLOY_DIR>/docker-compose.yml`。
服务器上不需要仓库其余部分，只要这个文件和 `.env` 在同一个目录里。

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

填写时注意四点：

1. `AUTH_COOKIE_SECURE=true`（生产走 HTTPS，必须）。
2. `DATABASE_URL` 指远程库。**不要**写 `localhost`——容器里的 `localhost` 指容器自己，
   不是宿主机。
3. `AUTH_ADMIN_EMAIL` 与 `AUTH_ADMIN_PASSWORD` 必须同时存在，密码 12–128 字符、不能与
   邮箱相同。模板里的尖括号是占位符，必须替换。
4. **不要写 `LLM_CHECKPOINT_POOL_SIZE`**。它在 `config/llm.py` 里声明为 `strict=True`，
   而 compose 的 `env_file` 注入的一律是字符串，配上会让容器启动即 `ValidationError`。

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
  用户在 `/admin/users` 按 F5 刷新会 404 白屏。
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
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend agent-lab init-checkpointer
docker compose up -d
docker compose logs -f backend
```

## 二、日常部署

推代码到 `main` 即自动部署。也可以在 GitHub 的 Actions 页面手动触发
（`workflow_dispatch`），用于「服务器侧改了配置想重跑一次」而不必造空提交。

CI 的完整顺序在 [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) 里，
几个顺序约束是有意的，不要调整：

1. **测试在构建之前**：任何测试失败就不构建、不推送、不部署。
2. **迁移在 `up -d` 之前**，且 `alembic upgrade head` 在 `agent-lab init-checkpointer`
   之前。理由见 [ADR 0004](adr/0004-checkpointer-tables-outside-alembic.md)。
3. **后端部署在前端上传之前**：迁移失败时部署中止，前端仍是旧版本，不会出现「新前端调
   老后端」。
4. **`docker compose pull` 不能省**：tag 恒为 `backend-latest`，`up -d` 认为 tag 没变会
   直接复用本地旧镜像——表现是 CI 全绿、容器也重启了，但跑的还是上一版代码。

## 三、排查

### 看日志

```bash
cd /opt/agent-lab
docker compose logs --tail 100 backend      # 最近 100 行
docker compose logs -f backend              # 跟踪
docker compose ps                           # 容器状态
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

当前只推 `backend-latest`，不打版本 tag，所以没有现成的回滚路径。要回到上一版：把对应
commit 重新推到 `main`（或在 Actions 页面对旧 commit 手动触发 `workflow_dispatch`），
重新构建一次。

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

# 停止（不会被 restart 策略自动拉起）
docker compose stop backend
```

`run --rm` 起的是一次性容器，用完即删，不影响正在服务的那个。
