#!/bin/bash
set -e

# 读取环境变量，默认 1（本地开发）
WORKER_COUNT=${WORKER_COUNT:-1}

echo "启动 uvicorn，worker 数量: $WORKER_COUNT"

# exec 替换掉 shell 进程，让 uvicorn 成为 PID 1
# 这样 Docker 的 SIGTERM 信号能正确传递，实现优雅关闭
exec uvicorn agent_lab.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "$WORKER_COUNT"
