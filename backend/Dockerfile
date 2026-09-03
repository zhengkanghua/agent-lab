# Agent Lab 后端镜像：多阶段构建，第一阶段装依赖，第二阶段只带运行期需要的东西。
#
# 为什么分两个阶段：uv 本身、编译工具链和构建缓存只在装依赖时需要，留在最终镜像里
# 纯属增重。分开之后最终镜像只有 Python 运行时 + .venv + 源码。
#
# 构建上下文是 backend/，不是仓库根目录。前端不在这个镜像里——dist 是静态文件，
# 由老板的 OpenResty 直接发，见 docs/adr/0008-backend-in-image-frontend-as-static-files.md。

# ---------- 第一阶段：装依赖 ----------
# uv 官方镜像自带 uv 和对应的 Python，标签里锁死 Python 版本，避免基础镜像升级时
# 悄悄换到 3.13——pyproject.toml 要求 >=3.12,<3.13，uv.lock 锁的是 ==3.12.*。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# 字节码预编译，换取运行时首次导入更快；容器是一次性的，编译产物不会污染宿主机。
ENV UV_COMPILE_BYTECODE=1 \
    # 依赖用拷贝而不是硬链接，避免跨文件系统挂载时 uv 报警告。
    UV_LINK_MODE=copy \
    # 只用 uv.lock 里锁定的版本，禁止构建时解析或升级依赖。
    UV_FROZEN=1

WORKDIR /app

# 先只拷依赖声明再装依赖，源码改动就不会让这一层缓存失效。
# --no-install-project：此时只装第三方依赖，本项目自己留到下一步，
# 这样改源码只重跑最后那一层，不用重装 langchain 全家桶。
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# README.md 必须拷：pyproject.toml 的 `readme = "README.md"` 会在构建包时读它，
# 缺文件直接构建失败。
COPY README.md ./
COPY src/ ./src/

# 装本项目自身（提供 agent_lab 包和 agent-lab 命令）。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- 第二阶段：运行 ----------
FROM python:3.12-slim-bookworm AS runtime

# PYTHONUNBUFFERED：让日志立刻进 docker logs，不然会被 Python 攒在缓冲区里，
# 排查时看不到最新几行。
# PYTHONDONTWRITEBYTECODE：运行期不再写 .pyc，构建阶段已经预编译过。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# 用非 root 跑。容器逃逸时 root 容器等于宿主机 root，这一层几乎零成本。
RUN groupadd --system --gid 1001 agentlab \
    && useradd --system --uid 1001 --gid agentlab --no-create-home agentlab

WORKDIR /app

# .venv 里的启动器（uvicorn、alembic、agent-lab）写死了 /app/.venv 这个绝对路径，
# 所以两个阶段的 WORKDIR 必须一致，否则拷过来的启动器找不到解释器。
COPY --from=builder --chown=agentlab:agentlab /app/.venv /app/.venv
COPY --chown=agentlab:agentlab src/ ./src/

# 拷贝启动脚本（支持 WORKER_COUNT 环境变量）
COPY --chown=agentlab:agentlab entrypoint.sh ./
RUN chmod +x entrypoint.sh

# alembic/ 与 alembic.ini 必须进镜像：部署时用 `docker compose run --rm backend
# alembic upgrade head` 迁移，跑的就是这个镜像。alembic.ini 里 sqlalchemy.url 是空的，
# alembic/env.py 直接 from agent_lab.config.settings 读连接串，所以它依赖上面装好的包。
COPY --chown=agentlab:agentlab alembic/ ./alembic/
COPY --chown=agentlab:agentlab alembic.ini ./

USER agentlab

EXPOSE 8000

# 不设 HEALTHCHECK：/health 会真查一次远程 PostgreSQL，而 compose 的 healthcheck 只影响
# 显示状态、不会自动重启，收益不足以换每 30 秒一次跨网查询。部署后由 CI 验证一次 /health。
#
# Linux 默认事件循环可直接用；README 里那个 --loop agent_lab.runtime:selector_loop_factory
# 只在 Windows 本地开发需要（Psycopg 异步驱动不支持 Proactor loop），容器里不要加。
CMD ["./entrypoint.sh"]
