---
status: accepted
---

# 后端进镜像，前端只当静态文件

后端打成 Docker 镜像推阿里云 ACR，甲骨文那台拉下来跑；前端不进容器，CI 把 `npm run build` 的产物
直接 rsync 到 1Panel 站点目录，由宿主机 OpenResty 发文件。反代、TLS 和限流全在 OpenResty，仓库不产出
任何 nginx 配置。具体流程、必需的 Variables/Secrets、以及所有踩过的坑见
[容器部署文档](../container_deployment.md)；工作流本身见 `.github/workflows/deploy.yml`。

**「前后端分离」是代码组织方式，不是部署方式**——两件事没有必然关系。后端是要跑起来的进程，有卡死的
Python 版本要求（`>=3.12,<3.13`）和几十个第三方依赖，而那台机器上还跑着别的服务，所以值得用镜像把它和
宿主机隔开。前端构建产物是一堆 HTML/JS/CSS：不运行、没有依赖、没有版本要求，**没有任何东西需要被隔离**。
给它套容器不产生收益，只是多一层壳——而且那层壳里必须再塞一个 web server（静态文件自己不监听端口，
容器没有前台进程会立刻退出），等于为了「看起来对称」凭空引入第二个 nginx。

## Considered Options

**前后端各一个容器，前端容器里放 nginx 发文件并把 `/api` 反代给后端。** 这是最常见的写法，两个镜像、
发版节奏独立，一份 compose 起全套，本地也能完整复现生产。但它要求容器里那个 nginx 和宿主机已有的
OpenResty 分工：`/api` 去前缀、History 模式的 `try_files` 回落这些规则放容器里，TLS 和限流留宿主机。
两层反代技术上不冲突，但机器上确实多了一个 nginx 进程和一份配置。本项目部署者同时是唯一维护者，
且已经在用 OpenResty 管着其他服务，多一个 web server 的认知成本高于「前端也容器化」的收益。

**一个容器装 nginx + uvicorn，用 supervisord 管两个进程。** 省一个镜像。但要自己管进程树（一个挂了
另一个不知道）、日志混在一起，而且前端改一行文案就要重发整个含 Python 全套依赖的几百 MB 镜像。

**后端容器顺便用 FastAPI 的 `StaticFiles` 发前端文件。** 一个容器、一个端口，反代只需一条规则。但要改
`main.py` 挂载静态目录并处理 History 模式回落，把前端构建产物塞进后端镜像，前后端发版从此绑死，也和
[AGENTS.md](../../AGENTS.md) 仓库约定 1（两个运行时各管各的依赖和构建）相冲。

**前端镜像只当搬运工：启动后把 `dist` 复制到宿主机挂载目录然后退出。** 能做到「前端容器化」且容器里
没有 nginx。但它把「容器」用成了文件传输工具，`docker ps` 里看不到前端（要 `docker ps -a` 才看到上次
执行记录），是个需要额外解释的非常规结构。

**后端也不用容器，CI 直接 rsync 代码，服务器上 `uv sync` + systemd 跑。** 环节最少，不需要 ACR，
日常部署更快。代价是生产机上要装 Python 3.12.x 和 uv 并维护 systemd 单元，后端与机器上其他服务共享
系统环境。这条路是正当的，被否掉只是因为版本隔离在这台混跑多服务的机器上更值钱；如果以后觉得 ACR
那一环太重，切回这套的成本不高。

## Consequences

**回滚要重新构建。** 只推 `backend-latest` 一个 tag，不打版本号，所以服务器上没有上一版镜像的名字
可用。要回滚就把旧 commit 重新推一次 `main`，或对它手动触发 `workflow_dispatch`。这是明确接受的取舍
（单人项目、未上线）。想改的话，让 CI 额外推一个 `sha-<commit>` tag 就够了，同一份镜像层不额外花存储。

**History 模式的 `try_files` 回落成了仓库管不到的配置。** 前端路由是 `createWebHistory()`，少了这条
回落，用户在 `/admin/users` 按 F5 会 404 白屏。它只存在于 OpenResty 里，仓库中没有任何东西能保证它被
配上，也没有测试能守住。这是「不产出 nginx 配置」的直接代价，只能靠部署文档写明。

**迁移不写成 compose 服务。** `alembic upgrade head` 和 `agent-lab init-checkpointer` 由部署脚本显式
执行（`docker compose run --rm`），不用 `depends_on: service_completed_successfully`——后者在迁移失败时
会让后端直接起不来，等于把「保守回退」换成「整站挂掉」。顺序约束来自
[ADR 0004](0004-checkpointer-tables-outside-alembic.md)。前后端在同一个工作流里顺序部署、后端先，
迁移失败则中止在后端那一步，前端仍是旧版本，不会出现「新前端调老后端」。
