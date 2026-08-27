# VPS 部署：Cloudflare + Nginx + Uvicorn

本文是 Agent Lab 当前实际部署模型的操作手册，不假设 Docker Compose，也不把
PostgreSQL、Ollama 或 Qdrant 搬进同一台机器。目标链路如下：

```text
浏览器
  -> Cloudflare DNS / Proxy / TLS
  -> VPS Nginx
       -> /                 frontend/dist 静态文件
       -> /api/*            去掉 /api 前缀后反代 127.0.0.1:8000
  -> Uvicorn（systemd，127.0.0.1:8000）
       -> PostgreSQL（本机或内网/受限远程）
       -> 远程 Ollama Embedding
       -> 远程 Qdrant
```

浏览器始终使用同域相对 API 前缀 `/api`。`VITE_*` 会被编译进公开的 JavaScript，不能放
数据库密码、FreshRSS 密码、Ollama/Qdrant API Key 或账号密码；服务端 Secret 只进入
后端进程。

## 1. 准备 VPS 与域名

以下示例假定部署目录为 `/opt/agent-lab`、运行用户为 `newsrag`、域名为
`news.example.com`。请把示例中的域名、路径、用户和远程服务地址替换为实际值。

1. 创建没有 shell 登录权限的 `newsrag` 用户，并把代码以该用户可读方式放到部署目录。
2. 防火墙只开放 SSH、80 和 443；不要把 Uvicorn 的 8000、PostgreSQL 5432、Ollama
   11434 或 Qdrant 端口暴露到公网。
3. Cloudflare DNS 的 A/AAAA 记录指向 VPS，并按需要开启 Proxy。
4. Cloudflare SSL/TLS 模式选择 **Full (strict)**，源站安装可信证书（例如 Certbot
   Let's Encrypt）。不要使用 Flexible，否则浏览器到 Cloudflare 与 Cloudflare 到源站
   的协议不一致，且生产 Cookie 不应退回非 HTTPS。
5. 若站点只允许经 Cloudflare 访问，应把源站 80/443 限制到 Cloudflare 官方 IP 段，或
   启用 Authenticated Origin Pulls；仅隐藏源站 IP 不是访问控制。

## 2. 安装依赖与构建

在发布版本目录执行。后端和前端依赖分别由各自目录管理：

```bash
cd /opt/agent-lab/backend
uv sync --frozen --no-dev

cd /opt/agent-lab/frontend
npm ci
npm run build
```

构建后确认 `frontend/dist` 存在。前端不需要设置 `VITE_API_BASE_URL`；默认的 `/api` 会
由 Nginx 同域转发。如果确实需要改变前端代理行为，只使用构建时公开的非 Secret 值。

## 3. 配置后端 Secret

本地开发可以编辑 `backend/.env`；VPS 推荐把部署变量放在代码目录之外，例如
`/etc/agent-lab/backend.env`，由 systemd 注入。文件内容遵守 dotenv/EnvironmentFile
格式：

```dotenv
DATABASE_URL=postgresql+psycopg://<db-user>:<db-password>@<db-host>:5432/news_vector_lc
DATABASE_TIMEZONE=UTC

AUTH_COOKIE_NAME=news_auth
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=strict
AUTH_SESSION_LIFETIME_SECONDS=28800

AUTH_ADMIN_EMAIL=admin@example.com
AUTH_ADMIN_PASSWORD=<从密码管理器生成的12到128字符随机秘密>

FRESHRSS_PROVIDER_KEY=freshrss_main
FRESHRSS_API_BASE_URL=https://freshrss.internal.example/api/
FRESHRSS_USERNAME=<freshrss-user>
FRESHRSS_API_PASSWORD=<freshrss-secret>

OLLAMA_BASE_URL=https://ollama.internal.example
OLLAMA_API_KEY=<ollama-secret-if-required>
OLLAMA_EMBEDDING_MODEL=bge-m3:567m

QDRANT_BASE_URL=https://qdrant.internal.example
QDRANT_API_KEY=<qdrant-secret-if-required>
QDRANT_ENVIRONMENT=production
QDRANT_COLLECTION_SCHEMA_VERSION=v1
QDRANT_COLLECTION_GENERATION=1
QDRANT_VECTOR_DIMENSION=1024
QDRANT_DISTANCE=Cosine
```

`AUTH_ADMIN_EMAIL` 与 `AUTH_ADMIN_PASSWORD` 必须同时存在。密码长度必须为 12–128 个
字符，且不能与邮箱相同；上面的尖括号只是占位符，部署前必须替换，不能原样使用。

把 Secret 文件限制为只有运行用户可读，并确认不会被 Nginx、Git、备份脚本日志或前端
构建读取：

```bash
sudo install -d -o newsrag -g newsrag -m 0750 /etc/agent-lab
sudo chown newsrag:newsrag /etc/agent-lab/backend.env
sudo chmod 0600 /etc/agent-lab/backend.env
```

如果使用其他 Secret 管理器，等价地把变量注入 Uvicorn 进程即可；不要把所有用户写成
JSON 放进 `.env`。`.env` 只负责一个保底管理员，日常账号由网页管理。

## 4. 先迁移数据库，再启动服务

每次发布包含后端 migration 时，先在唯一的发布步骤执行 migration，再重启 Uvicorn。不要
让多个 FastAPI Worker 在启动钩子中同时运行 Alembic：

```bash
sudo systemd-run --wait --pipe --collect \
  --unit=agent-lab-migrate \
  --property=User=newsrag \
  --property=WorkingDirectory=/opt/agent-lab/backend \
  --property=EnvironmentFile=/etc/agent-lab/backend.env \
  /opt/agent-lab/backend/.venv/bin/alembic upgrade head
```

这样由 systemd 解析同一个 Secret 文件，不把变量展开到命令参数，也不依赖 `grep | xargs`
处理密码中的空格或特殊字符。使用其他发布系统时保持同样原则：把 Secret 作为进程环境
注入，再单独执行 `.venv/bin/alembic upgrade head`。

应用当前认证 migration head 为 `b7e1a4c9d203`。migration 成功后再启动/重启服务；若
迁移失败，保留旧进程并修复数据库，不要让半迁移状态的应用接收流量。

## 5. systemd 服务

创建 `/etc/systemd/system/agent-lab.service`：

```ini
[Unit]
Description=Agent Lab API
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=newsrag
Group=newsrag
WorkingDirectory=/opt/agent-lab/backend
EnvironmentFile=/etc/agent-lab/backend.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/agent-lab/backend/.venv/bin/uvicorn agent_lab.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-lab
sudo systemctl status agent-lab
sudo journalctl -u agent-lab -n 100 --no-pager
```

Linux 默认事件循环可直接运行；Windows 本地开发需按项目 README 使用
`agent_lab.runtime:selector_loop_factory`，因为 Psycopg 异步驱动不支持
Proactor loop。systemd 环境不需要该 Windows 参数。

先在 VPS 本机检查：

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/openapi.json >/tmp/agent-lab-openapi.json
```

## 6. Nginx 同域静态资源与 `/api` 反代

在 `/etc/nginx/conf.d/agent-lab-login-limit.conf`（`http` 上下文）加入登录限流区域：

```nginx
limit_req_zone $binary_remote_addr zone=news_rag_login:10m rate=5r/m;
```

创建站点配置（证书路径按 Certbot 或实际证书调整）：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name news.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name news.example.com;

    ssl_certificate /etc/letsencrypt/live/news.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/news.example.com/privkey.pem;

    root /opt/agent-lab/frontend/dist;
    index index.html;
    client_max_body_size 1m;

    location = /api/auth/login {
        limit_req zone=news_rag_login burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000/auth/login;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 末尾斜杠会去掉 /api 前缀：/api/auth/me -> /auth/me。
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

执行配置检查和加载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Cloudflare 代理时，Nginx 看到的 `$remote_addr` 可能是 Cloudflare 节点地址。生产应按
Cloudflare 官方 IP 段配置 `real_ip` 模块，只信任来自 Cloudflare 的
`CF-Connecting-IP`，再让 `limit_req_zone` 使用真实客户端 IP；不要盲目信任公网请求自带的
同名 header。若暂时未配置 real IP，限流仍能工作，但可能按 Cloudflare 节点而非用户聚合。

## 7. 首次登录与日常账号管理

1. 访问 `https://news.example.com/login`。
2. 使用 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 登录；服务启动同步会在数据库中创建
   该账号，无需先运行 CLI。
3. 从搜索页顶部的“账号管理”进入 `/admin/users`。
4. 创建普通用户或其他超级用户；启用/停用、授权、重置密码和撤销会话都在页面完成。
5. 普通用户不会看到管理入口；即使手动访问 URL，后端也会返回 403。

页面不会显示、保存或回显任何密码。账号停用、密码重置和主动撤销会话会删除
`access_tokens`，旧浏览器下一次请求立即失效。

## 8. 轮换与恢复规则

修改 `/etc/agent-lab/backend.env` 后重启服务：

- 修改 `AUTH_ADMIN_PASSWORD`：启动同步 Argon2 Hash；若密码真的变化，撤销该账号所有
  现有会话。新密码可立即登录。
- 修改 `AUTH_ADMIN_EMAIL`：新邮箱被创建/同步为唯一环境管理员；旧邮箱账号保留其密码、
  active 和 superuser 状态，仍可由新管理员在网页管理，不会被删除或自动降权。
- 删除 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 两行：启动释放环境托管标记，但不删
  除、不降级旧账号；它仍按数据库中的普通超级用户规则存在。
- 只删除其中一项：配置校验失败，服务不会以半配置状态启动；请同时恢复两项或同时移除。

推荐的轮换顺序是：先确认新 Secret 已写入并备份，再执行唯一 migration（如版本有变化），
最后 `sudo systemctl restart agent-lab`，随后检查 `/health`、登录和
`journalctl`。不要把密码写进命令行参数、shell history、截图或工单。

CLI 恢复只在网页无法进入时使用：

```bash
sudo -u newsrag /opt/agent-lab/backend/.venv/bin/agent-lab \
  create-user --email recovery@example.com --superuser
```

恢复后应尽快登录网页创建/修复账号，并按需要撤销恢复账号会话；CLI 不用于把所有账号
配置塞进 `.env`。

## 9. 发布检查清单

每次发布至少执行：

```bash
cd /opt/agent-lab/backend
uv sync --frozen --no-dev
uv run --frozen --no-dev alembic upgrade head
uv run --frozen --no-dev alembic check

cd /opt/agent-lab/frontend
npm ci
npm run typecheck
npm run lint
npm run test:run
npm run build

sudo systemctl restart agent-lab
sudo nginx -t && sudo systemctl reload nginx
curl --fail https://news.example.com/api/health
```

确认浏览器桌面和移动视口都能登录、搜索、退出；超级用户能创建/停用/重置普通账号；
环境管理员的受保护按钮禁用；普通用户无法进入管理页；Local Storage 和 Session Storage
为空。数据库备份、Cloudflare 防火墙/限流和日志轮换应作为 VPS 的独立运维职责持续执行。
