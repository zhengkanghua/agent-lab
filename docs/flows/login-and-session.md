# 登录、会话与路由守卫

跨 `main.ts`、`api/client.ts`、`api/auth.ts`、`features/auth/auth-session.ts`、`app/router.ts`
五个文件。单看任何一个都拼不出全过程，接缝在 `main.ts` 里。

## 身份靠 Cookie，不靠前端存的 token

后端用 FastAPI Users 的 Cookie backend 发 HttpOnly 登录 Cookie。前端**读不到也存不了**它，
`api/client.ts` 只是在每个请求上带 `credentials: 'same-origin'` 让浏览器自动附带。

所以前端的 `authSession` 不是「身份的存放处」，只是**后端身份状态的一份本地缓存**。真相在
Cookie 和后端，前端这份随时可能过期。

## 启动到进页面

```
router.beforeEach ──► authSession.initialize() ──► GET /auth/me
                                                     │
                            ┌────────────────────────┴─────────┐
                          成功                                失败/401
                            │                                   │
                     status=authenticated                 status=anonymous
```

`app/router.ts` 的 `beforeEach` **每次导航都先 await `initialize()`**，再判断权限。`initialize`
内部有两道防抖：状态已知就直接返回，并发调用共用同一个 in-flight Promise（`auth-session.ts`
的 `initialize()`），所以不会每次跳转都打一次 `/auth/me`。

三条判断：

| 情况 | 结果 |
| --- | --- |
| 未登录访问 `requiresAuth` 路由 | 跳 `/login`，原路径放进 `query.redirect` |
| 已登录但非超级用户访问 `requiresSuperuser` 路由 | 跳 `/`（不是报错页） |
| 已登录访问 `/login` | 跳 `/` |

路由的 `meta` 是唯一的权限声明处。**后端有自己独立的一套依赖校验**（`main.ts` 的
`include_router` 处），前端守卫只管界面体验，不是安全边界——绕过它也拿不到数据。

## 登录

`login()` 是两步，不是一步：先 `POST /auth/login`，再**立刻 `GET /auth/me`** 确认会话真的建立了
（`auth-session.ts` 的 `login()`）。第二步没变成 authenticated 就抛错。

这么做是因为登录接口返回 200 只说明 Cookie 发出来了，不代表后续请求真能带上它（跨域、
Cookie 属性、代理都可能出问题）。多这一次往返换来的是「登录成功」这个状态可信。

`/auth/me`、`/auth/login`、`/auth/logout` 三个调用都显式关掉了 401 通知
（`api/auth.ts` 的 `notifyUnauthorized: false`）——它们**本来就可能 401**，那是正常返回值，
不是会话失效，不该触发下面的全局踢出。

## 会话中途失效

这是最容易看漏的一根线，接线点在 `main.ts` 的 `setUnauthorizedHandler()` 调用：

```
任意请求收到 401 ──► client.ts 的 unauthorizedHandler
                        │
                        ├─ authSession.expire()      清本地身份
                        ├─ queryClient.clear()       清缓存，防止串号
                        └─ router.replace('/login')  带上 redirect
```

`api/client.ts` 只负责「发现 401 就喊一声」，它不知道有路由和会话；具体怎么响应由 `main.ts`
在启动时注入。这样 `client.ts` 不依赖 router 和 auth，测试里也能单独换掉这个 handler
（`client.spec.ts` 的 `notifies the application when an authenticated API request returns 401`）。

`queryClient.clear()` 不能省。不清缓存的话，换账号登录后可能看到上一个账号的检索结果。

## 退出

`logout()` 把 401 当成成功（`auth-session.ts` 的 `logout()`）：会话本来就已经没了，目的已经达到，
不该给用户报错。

## 边界

- 没有注册、密码重置、邮箱验证的对外入口。建账号走 CLI `create-user` 或超级用户后台。
- 前端不解析、不缓存、不刷新 token。没有 refresh token 机制。
