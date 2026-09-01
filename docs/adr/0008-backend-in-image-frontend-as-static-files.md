# 后端进镜像，前端只当静态文件

后端打成 Docker 镜像推阿里云 ACR，甲骨文那台拉下来跑；前端不进容器，CI 把 `npm run build`
的产物直接 rsync 到 1Panel 站点目录，由宿主机 OpenResty 发文件。反代、TLS 和限流全在
OpenResty，仓库不产出任何 nginx 配置。

「前后端分离」是仓库的代码组织方式，不是部署方式——两件事没有必然关系。后端是个要跑起来的
进程，有卡死的 Python 版本要求（`>=3.12,<3.13`）、几十个第三方依赖，而那台机器上还跑着别的
服务，所以值得用镜像把它和宿主机隔开。前端构建产物是一堆 HTML/JS/CSS：不运行、没有依赖、
没有版本要求，**没有任何东西需要被隔离**。给它套容器不产生任何收益，只是多一层壳——而且那层壳
里必须再塞一个 web server（静态文件自己不监听端口，容器没有前台进程会立刻退出），等于为了
「看起来对称」凭空引入第二个 nginx。

## Considered Options

**前后端各一个容器，前端容器里放 nginx 发文件并把 `/api` 反代给后端。** 这是最常见的写法，
两个镜像、发版节奏独立，一份 compose 起全套，本地也能完整复现生产。但它要求容器里那个 nginx
和宿主机已有的 OpenResty 分工：`/api` 去前缀、History 模式的 `try_files` 回落这些规则放容器里，
TLS 和限流留宿主机。两层反代技术上不冲突（容器有独立网络空间，容器内的 80 和宿主机的 80 互不
可见），但机器上确实多了一个 nginx 进程和一份配置。本项目的部署者同时是唯一维护者，且已经在
用 OpenResty 管着其他服务，多一个 web server 的认知成本高于「前端也容器化」带来的收益。

**一个容器装 nginx + uvicorn，用 supervisord 管两个进程。** 省一个镜像。但要自己管进程树
（一个挂了另一个不知道）、日志混在一起，而且前端改一行文案就要重发整个含 Python 全套依赖的
几百 MB 镜像。等于自建一个没人监控的进程管理器。

**后端容器顺便用 FastAPI 的 `StaticFiles` 发前端文件。** 一个容器、一个端口，反代只需一条规则。
但要改 `main.py` 挂载静态目录并处理 History 模式回落，把前端构建产物塞进后端镜像，前后端发版
从此绑死，也和 `AGENTS.md` 仓库约定 1（两个运行时各管各的依赖和构建）相冲。用 uvicorn 发静态
文件本身也不是它擅长的事。

**前端镜像只当搬运工：启动后把 `dist` 复制到宿主机挂载目录然后退出。** 能做到「前端容器化」
且容器里没有 nginx，投递方式还统一在镜像这一条路上。但它把「容器」用成了文件传输工具，
`docker ps` 里看不到前端（要 `docker ps -a` 才看到上次执行记录），是个需要额外解释的非常规结构。
既然直接 rsync 就能达到同样效果，这层间接没有换来任何东西。

**后端也不用容器，CI 直接 rsync 代码，服务器上 `uv sync` + systemd 跑**（即
`docs/vps_deployment.md` 那套）。环节最少，不需要 ACR，日常部署更快（传几 KB 代码 vs 传几 MB
镜像层）。代价是生产机上要装 Python 3.12.x 和 uv 并维护 systemd 单元，后端与机器上其他服务共享
系统环境。这条路是正当的，被否掉只是因为版本隔离在这台混跑多服务的机器上更值钱；如果以后觉得
ACR 那一环太重，切回这套的成本不高。

## Consequences

**回滚要重新构建。** 只推 `backend-latest` 一个 tag，不打版本号，所以服务器上没有上一版镜像的
名字可用。要回滚就把旧 commit 重新推一次 `main`，或对它手动触发 `workflow_dispatch`。这是明确
接受的取舍（单人项目、未上线），不是遗漏。想改的话，让 CI 额外推一个 `sha-<commit>` tag 就够了，
同一份镜像层不额外花存储。

**`docker compose pull` 变成正确性的一部分，不只是优化。** tag 恒为 `backend-latest` 时，
`up -d` 认为 tag 没变就直接复用本地旧镜像。漏掉 pull 的表现是 CI 全绿、容器也重启了，但跑的还是
上一版代码——没有任何报错。这一步写死在工作流里，删了它不会有人立刻发现。

**History 模式的 `try_files` 回落成了仓库管不到的配置。** 前端路由是 `createWebHistory()`，
少了这条回落，用户在 `/admin/users` 按 F5 会 404 白屏。它现在只存在于 OpenResty 里，仓库中没有
任何东西能保证它被配上，也没有测试能守住。这是「不产出 nginx 配置」的直接代价，只能靠
`docs/container_deployment.md` 里写明。

**迁移不写成 compose 服务。** `alembic upgrade head` 和 `agent-lab init-checkpointer` 由部署脚本
显式执行（`docker compose run --rm`），不用 `depends_on: service_completed_successfully`。原因是
迁移失败时要保留旧容器继续服务；`depends_on` 那种写法在迁移失败时会让后端直接起不来，等于把
「保守回退」换成「整站挂掉」。顺序约束本身来自
[ADR 0004](0004-checkpointer-tables-outside-alembic.md)。

**前后端在同一个工作流里顺序部署，后端先。** 迁移失败则中止在后端那一步，前端仍是旧版本，
不会出现「新前端调老后端」。反过来先传前端就做不到这一点。

**ACR 个人版只认基本的 OCI manifest，所以工作流里关掉了 provenance / SBOM，缓存也不放 registry。**
buildx 从 v0.10 起默认给镜像附加来源证明和物料清单，它们在 manifest 里用
`application/vnd.oci.empty.v1+json` 占位；ACR 个人版不认识这个媒体类型，会在最后一步回
`denied: unknown manifest class for application/vnd.oci.empty.v1+json`。这个坑的麻烦之处在于
失败得很晚——所有层和镜像 manifest 都已经推成功，只有附加的证明 manifest 被拒，日志里前面全是
正常的 `writing layer`，很容易当成网络或权限问题去查。所以
`provenance: false` / `sbom: false` 是能不能推上去的问题，不是优化，别当成多余配置删掉。
同理，构建缓存用 `type=gha` 而不是 `type=registry`：后者要往 registry 写
`application/vnd.buildkit.cacheconfig.v0`，同一个 registry 已经拒了上面那个类型，没有理由赌它认这个。
换成支持完整 OCI 规范的 registry（ACR 企业版、GHCR、Harbor）后这三项都可以放开。
