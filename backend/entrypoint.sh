#!/bin/bash
set -e

# 读取环境变量，默认 1（本地开发）
WORKER_COUNT=${WORKER_COUNT:-1}

echo "启动 uvicorn，worker 数量: $WORKER_COUNT"

# exec 替换掉 shell 进程，让 uvicorn 成为 PID 1
# 这样 Docker 的 SIGTERM 信号能正确传递，实现优雅关闭
#
# --timeout-graceful-shutdown 30：收到退出信号后先停接新连接、等已有连接与任务结束，
# 到点（30 秒）才掐掉还没结束的连接与任务，然后走 ASGI 生命周期收尾。这个 30 秒是给
# **非 Agent 的慢请求**留的窗口（检索流几秒、文件上传整个落在请求里），运行已经与连接解耦，
# 所以那条正在生成的 SSE 连接被掐断不影响运行：收尾里会把它排空到可交接的边界，交给新进程
# 续跑（见 docs/adr/0040-run-handover-on-deploy.md）。取值连同下面的排空上限一起装在 API 容器
# 180 秒的停止宽限里（docker-compose.yml），不再跟着「一次运行能跑多久」走。
exec uvicorn agent_lab.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --timeout-graceful-shutdown 30 \
  --workers "$WORKER_COUNT"
