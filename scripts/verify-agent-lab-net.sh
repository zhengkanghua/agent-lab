#!/usr/bin/env bash
# 复核 agent-lab-net（或指定的另一张网）上能不能够到 redis / postgresql / minio。
#
# 用途与「线丢了」的症状对照见 docs/container_deployment.md 的「网络与容器内依赖」一节。把这份留在
# 仓库里，是为了面板重建了某个依赖容器、线丢了的时候能直接从仓库拿回来跑，而不必凭记忆重敲。
#
# 它会在目标网络上起一个一次性容器，真跑一遍：Redis PING（用应用自己的 RedisSettings 读 .env）、
# 两个 PostgreSQL 库各一次 SELECT 1、S3_ENDPOINT 的 DNS 解析与 TCP 连接。
#
# 用法：bash verify-agent-lab-net.sh [网络名]      # 默认 agent-lab-net
set -uo pipefail

NET=${1:-agent-lab-net}
DEPLOY_DIR=${DEPLOY_DIR:-/opt/1panel/docker/compose/agent-lab}

IMG=$(grep -E '^BACKEND_IMAGE=' "$DEPLOY_DIR/.env" | head -1 | cut -d= -f2- | tr -d '\r' || true)
[ -n "$IMG" ] || { echo "读不到 $DEPLOY_DIR/.env 里的 BACKEND_IMAGE"; exit 1; }

# 程序通过 -c 传给容器，不落临时文件、也不走 stdin：`docker run` 少了 -i 时 heredoc 会喂给 CLI 自己、
# 容器里的 python 会读到空程序并静默通过（这个坑在部署演练里踩过）。
PROGRAM=$(cat <<'PY'
"""真跑一遍三根线：Redis PING、两个 PostgreSQL 库各一次 SELECT 1、S3 端点的解析与 TCP。"""

import os
import socket
import sys
from urllib.parse import urlsplit

import psycopg
from redis import Redis
from sqlalchemy.engine import make_url

from agent_lab.config.redis import get_redis_settings

failures: list[str] = []


def report(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def split_addr(value: str, default_port: int) -> tuple[str, int]:
    url = urlsplit(value if "://" in value else "scheme://" + value)
    return url.hostname or value, url.port or default_port


try:
    settings = get_redis_settings()
    url = settings.url.get_secret_value()
    host, port = split_addr(url, 6379)
    with Redis.from_url(url,
                        password=settings.password.get_secret_value() or None,
                        socket_connect_timeout=5,
                        socket_timeout=5) as client:
        client.ping()
    report(f"Redis PING {host}:{port}", True)
except Exception as exc:
    report("Redis PING", False, f"{type(exc).__name__}: {exc}")

for key in ("DATABASE_URL", "LLMOPS_DATABASE_URL"):
    value = os.environ.get(key, "")
    if not value:
        report(f"{key} 未配置", False)
        continue
    parsed = make_url(value)
    label = f"{key} SELECT 1 {parsed.host}:{parsed.port or 5432}"
    try:
        with psycopg.connect(host=parsed.host,
                             port=parsed.port,
                             dbname=parsed.database,
                             user=parsed.username,
                             password=parsed.password,
                             connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        report(label, True)
    except Exception as exc:
        report(label, False, f"{type(exc).__name__}: {exc}")

endpoint = os.environ.get("S3_ENDPOINT", "")
if not endpoint:
    report("S3_ENDPOINT 未配置", False)
else:
    host, port = split_addr(endpoint, 9000)
    try:
        addr = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)[0][4][0]
        with socket.create_connection((host, port), timeout=5):
            pass
        report(f"S3_ENDPOINT TCP {host}:{port} -> {addr}", True)
    except Exception as exc:
        report(f"S3_ENDPOINT TCP {host}:{port}", False, f"{type(exc).__name__}: {exc}")

print("  => " + ("全部通过" if not failures else "有失败项：" + "、".join(failures)))
sys.exit(1 if failures else 0)
PY
)

echo "网络=$NET 镜像=$IMG"
docker run --rm --network "$NET" --env-file "$DEPLOY_DIR/.env" \
  --entrypoint python "$IMG" -c "$PROGRAM"
