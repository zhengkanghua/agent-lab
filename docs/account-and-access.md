# 域名接入与账号管理

部署之外的运营操作：域名与源站防护、网页账号的日常管理、以及恢复用超级用户的轮换规则。它与容器化无关，放在这里是为了让[容器部署手册](container_deployment.md)专心讲部署、回滚与排查。

## Cloudflare 与源站防护

1. Cloudflare DNS 的 A/AAAA 记录指向服务器，并按需要开启 Proxy。
2. Cloudflare SSL/TLS 模式选择 **Full (strict)**，源站安装可信证书（例如 Certbot Let's Encrypt）。不要使用 Flexible，否则浏览器到 Cloudflare 与 Cloudflare 到源站的协议不一致，且生产 Cookie 不应退回非 HTTPS。
3. 若站点只允许经 Cloudflare 访问，应把源站 80/443 限制到 Cloudflare 官方 IP 段，或启用 Authenticated Origin Pulls；仅隐藏源站 IP 不是访问控制。
4. 防火墙只开放 SSH、80 和 443；不要把后端 18000、PostgreSQL 5432、Ollama 11434 或 Qdrant 端口暴露到公网（端口现状见部署手册第一节第 5 小节与第三节的逃生路径）。

## 首次登录与日常账号管理

1. 访问 `https://<域名>/login`。
2. 使用 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 登录；服务启动同步会在数据库中创建该账号，无需先运行 CLI。
3. 从侧栏底部的“后台管理”进入 `/admin`，账号管理是后台的一个分区（`/admin/users`）。
4. 创建普通用户或其他超级用户；启用/停用、授权、重置密码和撤销会话都在页面完成。
5. 普通用户不会看到管理入口；即使手动访问 URL，后端也会返回 403。

页面不会显示、保存或回显任何密码。账号停用、密码重置和主动撤销会话会删除 `access_tokens`，旧浏览器下一次请求立即失效。

## 恢复用超级用户的轮换与恢复规则

修改 `<DEPLOY_DIR>/.env` 后**按部署手册第二节「手工跑一遍同一套流程」重新部署一次**（`docker stack deploy` 会把 `.env` 的新值注入服务定义并滚动更新，服务会自动重建）：

- 修改 `AUTH_ADMIN_PASSWORD`：启动同步 Argon2 Hash；若密码真的变化，撤销该账号所有现有会话。新密码可立即登录。
- 修改 `AUTH_ADMIN_EMAIL`：新邮箱被创建/同步为唯一带环境托管标记的超级用户；旧邮箱账号保留其密码、active 和 superuser 状态，仍可由新管理员在网页管理，不会被注销、也不会被自动改动角色。
- 删除 `AUTH_ADMIN_EMAIL` 和 `AUTH_ADMIN_PASSWORD` 两行：启动释放环境托管标记，但不注销、也不改动它的角色；它仍按数据库中的普通超级用户规则存在。
- 只删除其中一项：配置校验失败，服务不会以半配置状态启动；请同时恢复两项或同时移除。
- 配置的邮箱在库里是一个**已注销**的账号：启动直接失败并说明需要人工处理，不会把这个账号拉回成能登录的超管。这一行是注销的终态，启动同步不能撤销它；要恢复入口就换一个邮箱，或者人工处理库里那一行（第一版没有恢复入口）。

推荐的轮换顺序是：先确认新 Secret 已写入并备份，再执行迁移（如版本有变化），最后上面那套重新部署，随后检查 `/health`、登录和 `docker service logs --tail 50 agent-lab_backend`。不要把密码写进命令行参数、shell history、截图或工单。

CLI 恢复只在网页无法进入时使用（见部署手册「四、手动运维命令」的 `create-user`）；恢复后应尽快登录网页创建/修复账号，并按需要撤销恢复账号会话。CLI 不用于把所有账号配置塞进 `.env`。
