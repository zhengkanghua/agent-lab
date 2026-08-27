# 本地账号密码认证

平台使用 FastAPI Users 的 SQLAlchemy 适配器、CookieTransport 和 DatabaseStrategy，为已知
内部使用者提供普通账号密码登录。目标是阻止陌生人匿名使用搜索、全文和高成本写入接口，
不建设公开注册、邮件找回、OAuth、组织或通用 RBAC 系统。

当前采用一个简单的混合管理模型：部署配置只托管一个保底超级管理员，其他账号由已登录
超级用户在网页中维护；CLI 只作为网页不可用时的恢复工具。

## 请求链路

```text
backend/.env 或 systemd EnvironmentFile
    -> AUTH_ADMIN_EMAIL + AUTH_ADMIN_PASSWORD（必须成对）
    -> 应用启动时同步唯一 environment admin
    -> 首次登录后进入 /admin/users 管理其他账号

浏览器 POST /auth/login（form-urlencoded）
    -> 按大小写不敏感 email 查询 users
    -> 验证密码与 is_active
    -> PostgreSQL access_tokens 写入随机 Token
    -> Set-Cookie: HttpOnly; Secure; SameSite=Strict

浏览器请求受保护 API
    -> CookieTransport 读取 Cookie
    -> DatabaseStrategy 查询 Token 有效期和所属 user
    -> current_active_user / current_superuser 授权
```

浏览器 JavaScript 不能读取 HttpOnly Cookie，也不在 Local Storage、Session Storage 或 Vue
状态中保存 Token。Vue 的用户状态只来自 `GET /auth/me`，刷新页面时重新确认。前端路由
守卫只改善体验，后端依赖才是安全边界。

## 表与生命周期

`users` 一行代表一个内部人工账号：

- UUID 主键；
- `lower(email)` 唯一索引，与登录查询的大小写语义一致；
- `hashed_password` 只保存 pwdlib/Argon2 Hash；
- `is_active` 决定账号是否能登录和继续使用已有 Token；
- `is_superuser` 决定是否可执行 Pipeline 和账号管理；
- `is_verified` 由受信管理入口直接设为 true，不发送验证邮件；
- `is_environment_admin` 标记是否由启动环境 Secret 托管，数据库部分唯一索引保证全库最多
  一行，并要求该账号始终 active、verified、superuser。

`access_tokens` 一行代表一个浏览器登录。Token 是 43 字符高熵随机主键，带创建时间和
users 外键。DatabaseStrategy 按创建时间应用有效期；退出、停用、重置密码或管理员主动
撤销会话时删除 Token。删除用户时由外键级联撤销全部 Token。

## HTTP 契约

| 方法 | 路径 | 访问要求 | 成功 |
|---|---|---|---|
| POST | `/auth/login` | 公开，邮箱和密码表单 | 204 + Set-Cookie |
| POST | `/auth/logout` | 已登录 | 204 + 删除 Token/Cookie |
| GET | `/auth/me` | 已登录 | 最小用户 JSON |
| GET | `/admin/users` | active superuser | 安全账号列表 |
| POST | `/admin/users` | active superuser | 201 + 新账号 |
| PATCH | `/admin/users/{id}` | active superuser | 更新启用/超级用户状态 |
| POST | `/admin/users/{id}/password` | active superuser | 重置密码并撤销会话 |
| DELETE | `/admin/users/{id}/sessions` | active superuser | 撤销会话数量 |
| POST | `/vector-search` | active user | 原有响应不变 |
| POST | `/document-search` | active user | 原有响应不变 |
| GET | `/documents/{id}` | active user | 原有响应不变 |
| POST | `/pipeline/run-once` | active superuser | 原有响应不变 |
| GET | `/health` | 公开 | 基础设施探活 |

没有 `/auth/register`、公开密码找回、验证邮件或用户删除 API。环境托管管理员的启用、
超级用户和密码字段在网页/API 中返回稳定 `409 environment_admin_protected`，必须改部署
Secret 后重启。最后一个启用的超级用户不能被停用或降级。匿名访问受保护资源返回 401；
普通用户调用 Pipeline 或账号管理返回 403。

## 保底管理员配置

本地在 `backend/.env` 配置，生产用 systemd `EnvironmentFile` 或等价 Secret 注入：

```dotenv
AUTH_ADMIN_EMAIL=admin@example.com
AUTH_ADMIN_PASSWORD=<12-128-character-secret>
AUTH_COOKIE_SECURE=true
```

模板 `backend/.env.example` 只保留注释占位符，不包含可直接登录的默认密码。两个
`AUTH_ADMIN_*` 必须同时存在；密码长度必须为 12–128 个字符，且不能与邮箱相同。密码由
`SecretStr` 接收，启动同步只把它交给密码 Hash/校验器，不进入日志、异常、OpenAPI 或响应。

启动同步行为如下：

1. 两个变量都没有：释放旧的 `is_environment_admin` 标记，但保留该账号原有密码、启用状态
   和超级用户角色，不删除账号。
2. 账号邮箱不存在：创建 active、verified、superuser 的新账号，并标记为环境管理员。
3. 邮箱已存在：校验并按需要更新密码 Hash，同时强制 active、verified、superuser。
4. 密码发生变化：删除该账号全部 `access_tokens`，让旧浏览器会话立即失效。
5. 邮箱发生变化：新邮箱成为唯一环境管理员；旧邮箱账号保留为普通可管理的超级用户，
   不会被删除或自动降权。

启动同步在构造搜索 Runtime 前访问 PostgreSQL；迁移未完成或数据库不可用时进程启动失败，
部署必须先执行 Alembic migration。并发启动遇到唯一约束时服务会进行一次安全重试。

## 网页账号管理与 CLI 恢复

保底管理员登录后，顶部会显示“账号管理”入口 `/admin/users`。页面可以：

- 创建普通账号或超级用户；
- 启用/停用普通账号；
- 授予/撤销普通账号的超级用户权限；
- 重置普通账号密码（同时撤销该账号全部会话）；
- 主动撤销任意账号的全部会话。

页面不显示密码，不保存密码，不提供删除账号操作。环境托管行的启用、降权和重置按钮
禁用，但允许主动撤销其会话。后端 Service 会再次执行相同保护，不能依赖前端隐藏按钮。

首次部署通常不再需要 CLI：

```powershell
uv sync
uv run alembic upgrade head
# 编辑 .env 后直接启动服务，AUTH_ADMIN_* 会自动创建保底管理员
```

CLI 仍可作为恢复入口（例如误停用全部网页超级用户、Secret 暂时不可用时）：

```powershell
uv run agent-lab create-user --email recovery@example.com --superuser
```

CLI 使用终端隐藏输入并要求重复确认，密码不会进入命令参数、Shell 历史或 JSON 输出。它
不负责同步 `is_environment_admin` 标记，也不应被当作日常批量账号配置界面。

## 迁移、部署与安全边界

认证表结构由 Alembic revision `b7e1a4c9d203` 管理：

```powershell
uv run alembic upgrade head
uv run alembic check
```

生产必须使用 HTTPS 并保持 `AUTH_COOKIE_SECURE=true`。只有 Vite + Uvicorn 的本地 HTTP 联调
才临时设为 false。同域 `/api` 和 SameSite=Strict 会阻止标准跨站请求携带 Cookie；生产网关
仍须限制 `/api/auth/login` 频率，并设置请求体、并发和 timeout 上限。Cookie 登录不替代
Cloudflare/Nginx 源站访问控制、数据库备份或安全日志。

浏览器认证 Cookie 与 `OLLAMA_API_KEY`、`QDRANT_API_KEY`、FreshRSS 密码完全不同，后者
只是服务访问上游的凭据，不能当作登录密钥。`VITE_*` 值会进入公开构建产物，任何 Secret
都不得写入其中。

平台实际 VPS 链路、systemd Secret、Nginx `/api` 代理、Cloudflare Full (strict) TLS 和
轮换操作见 [`docs/vps_deployment.md`](../../../docs/vps_deployment.md)。

## 测试边界

离线测试覆盖：

- 登录设置 Secure/HttpOnly/SameSite Cookie；
- `/auth/me` 只返回安全用户字段；
- 退出删除 Token 后会话立即失效；
- 环境配置成对校验、Secret 脱敏和密码强度；
- 匿名搜索为 401，普通用户 Pipeline/账号管理为 403；
- 账号管理 API 的请求/响应、环境管理员保护和最后超级用户保护；
- CLI 两次密码确认、最低强度和输出脱敏；
- 原搜索、全文和 Pipeline 行为测试通过显式依赖覆盖继续验证自身契约。

真实 PostgreSQL 测试可显式运行：

```powershell
$env:RUN_POSTGRES_AUTH_INTEGRATION_TEST="1"
uv run pytest -q tests/test_auth_environment_integration.py
```

测试使用外层事务和随机邮箱/Token，结束时回滚临时数据。OpenAPI 变化后只能从运行中的
`/openapi.json` 重新生成前端类型。
