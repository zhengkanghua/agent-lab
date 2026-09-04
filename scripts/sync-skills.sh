#!/usr/bin/env bash
# 把 .codex/skills/（唯一源）镜像到三份运行时副本。
#
# .claude/、.zcode/、.gemini/ 各自的 skills/ 是 .codex/skills/ 的副本，各工具只读自己
# 目录（约定见根 AGENTS.md「仓库约定」）。脚本是全量镜像：源里删掉的 skill 在副本里
# 一并删除；diff 校验无输出即一致，输出「已同步」即成功。
set -euo pipefail
cd "$(dirname "$0")/.."

for dir in .claude .zcode .gemini; do
  rm -rf "$dir/skills"
  cp -r .codex/skills "$dir/skills"
  diff -r .codex/skills "$dir/skills" && echo "$dir/skills 已同步"
done
