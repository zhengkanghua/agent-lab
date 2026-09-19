#!/usr/bin/env bash
# 把 .codex/skills/（唯一源）镜像到四份运行时副本。
#
# .claude/、.zcode/、.gemini/、.pi/ 各自的 skills/ 是 .codex/skills/ 的副本，各工具只读
# 自己目录（约定见根 AGENTS.md「仓库约定」）。脚本是全量镜像：源里删掉的 skill 在副本里
# 一并删除；diff 校验无输出即一致，输出「已同步」即成功。
#
# .pi 是 pi 的项目级位置（pi 递归发现 `.pi/skills/**/SKILL.md`）。注意**不要**改用
# `.agents/skills`：那是跨工具的标准位置，Codex 也读它（codex.exe 里同时存在
# `.codex/skills` 与 `.agents/skills` 两个字面量），副本放那里会让 Codex 看到两份同名
# skill。`.pi` 是 pi 私有目录，不影响其他工具。
#
# 代价：`.pi/skills` 属于「项目本地资源」，pi 下次交互启动会问一次是否信任本项目
# （答案记在 ~/.pi/agent/trust.json）。
set -euo pipefail
cd "$(dirname "$0")/.."

for dir in .claude .zcode .gemini .pi; do
  # .pi/ 等目录可能还不存在（首次运行时），先建出来再镜像。
  mkdir -p "$dir"
  rm -rf "$dir/skills"
  cp -r .codex/skills "$dir/skills"
  diff -r .codex/skills "$dir/skills" && echo "$dir/skills 已同步"
done
