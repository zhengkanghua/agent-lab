#!/usr/bin/env bash
# 应急菜单：线上出问题时最常用的四件事。
#
# 用法：把本文件放到部署目录（或任意位置），然后
#     bash emergency-ops.sh [部署目录]
# 省略参数时用当前目录，因此「在部署目录下直接运行」也可以。部署目录必须能找到 .env。
#
# 只做四件事：回上一版、切到指定版本、重启某个服务、看现状并清垃圾容器。
# 每个会改动线上状态的动作都会先让你确认一次。改完的形状仍以 CI 部署为准（见 ADR 0042）。

set -euo pipefail

STACK_NAME="agent-lab"
SERVICES=("${STACK_NAME}_backend" "${STACK_NAME}_task-beat" "${STACK_NAME}_task-worker")

DEPLOY_DIR="${1:-$(pwd)}"
cd "$DEPLOY_DIR"
if [ ! -f .env ]; then
  echo "在 $DEPLOY_DIR 里找不到 .env；请在部署目录下运行，或把部署目录作为第一个参数传入。" >&2
  exit 1
fi

# 当前正在跑的镜像引用（带 @sha256 才算摘要被钉住，秒级回滚才有意义）。
current_image() { docker service inspect "$1" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'; }
previous_image() { docker service inspect "$1" --format '{{.PreviousSpec.TaskTemplate.ContainerSpec.Image}}' 2>/dev/null || true; }

show_status() {
  echo "--- 服务：期望副本与实际副本"
  docker service ls --filter "name=${STACK_NAME}"
  echo
  echo "--- 本栈的容器（服务层看着是 1/1、这里却多出同名容器 = 孤儿）"
  docker ps --filter "name=${STACK_NAME}_" --format '{{.Names}}\t{{.Status}}'
  echo
  echo "--- API 当前的镜像引用"
  current_image "${SERVICES[0]}"
}

rollback_all() {
  echo "当前镜像：$(current_image "${SERVICES[0]}")"
  echo "上一版镜像：$(previous_image "${SERVICES[0]}")"
  read -r -p "把三个服务都回滚到上一版？(yes/no) " answer
  [ "$answer" = yes ] || { echo "已取消。"; return 0; }
  for service in backend task-beat task-worker; do
    docker service update --rollback "${STACK_NAME}_${service}"
  done
  echo "--- 回滚后的镜像引用（应变成上面那个「上一版」）"
  current_image "${SERVICES[0]}"
}

switch_image() {
  echo "当前镜像：$(current_image "${SERVICES[0]}")"
  echo "上一版镜像：$(previous_image "${SERVICES[0]}")"
  echo
  echo "--- 本地留下的无标签镜像（回滚候选，够回滚用）"
  docker images --filter dangling=true --format '{{.ID}} {{.CreatedSince}}'
  echo
  echo "提示：完整地址形如 <仓库名>:backend-latest@sha256:<摘要>；摘要必须来自具体那一版，"
  echo "      不要用可变的 backend-latest 标签。"
  read -r -p "要切到哪个地址（直接回车取消）？" reference
  [ -n "$reference" ] || { echo "已取消。"; return 0; }
  read -r -p "用 $reference 更新 ${SERVICES[0]}？(yes/no) " answer
  [ "$answer" = yes ] || { echo "已取消。"; return 0; }
  docker service update --image "$reference" "${SERVICES[0]}"
}

restart_service() {
  echo "重启哪个服务（是「服务」，不是「容器」——重启容器会留下孤儿）？"
  select service in "${SERVICES[@]}"; do
    [ -n "${service:-}" ] || { echo "请输入 1-3。"; continue; }
    read -r -p "强制重启 $service？(yes/no) " answer
    [ "$answer" = yes ] || { echo "已取消。"; return 0; }
    docker service update --force "$service"
    return 0
  done
}

cleanup_containers() {
  echo "--- 本栈已退出的任务容器"
  # grep 在没有匹配时会返回非零，这里必须让它与 set -e 和平共处。
  exited="$(docker ps -a --filter "name=${STACK_NAME}_" --filter status=exited --format '{{.Names}}')"
  if [ -z "$exited" ]; then
    echo "（没有已退出的容器）"
  else
    echo "$exited"
    read -r -p "清掉它们？(yes/no) " answer
    if [ "$answer" = yes ]; then
      echo "$exited" | xargs -r docker rm
      echo "已清理。"
    else
      echo "已取消。"
    fi
  fi
  echo
  echo "注意：服务层看着干净、docker ps 里却多出同名容器，那是孤儿（重启过容器层留下的），"
  echo "      用 docker rm -f <容器名> 清掉。"
}

while true; do
  cat <<'MENU'

=== agent-lab 应急菜单 ===
  1) 回上一版（三个服务一起回滚，约几秒）
  2) 切到指定版本（手动给带 @sha256 的完整地址）
  3) 重启某个服务
  4) 看现状 / 清已退出的容器
  5) 退出
MENU
  read -r -p "选一个 [1-5]：" choice
  case "$choice" in
    1) rollback_all ;;
    2) switch_image ;;
    3) restart_service ;;
    4) show_status; echo; cleanup_containers ;;
    5) exit 0 ;;
    *) echo "请输入 1-5。" ;;
  esac
done
