# 中文化迁移工具链归档到仓库外

2026-08-27 清理仓库时，把后端消息中文化迁移的一次性工具链归档到仓库外的
`D:\kh\xm\agent-lab-archive\backend-i18n-migration-2026-08-24\`，然后删除了仓库内的
`output/`（25MB / 776 文件）和 `.playwright-cli/`。

这两个目录都在 `.gitignore` 里，git 从未保存过它们，所以删除后仓库里不留任何线索——
不归档就是永久丢失。值得留的只有 `translate_tools/translate_msgs.py` 里那张 33KB 的硬编码
译文映射表：它是「哪句英文对应哪句中文」的唯一记录，是后端所有错误 `detail` 措辞的来源依据。
其余（24MB playwright 走查截图、uvicorn 访问日志）是特定时刻的快照，后续 CSS 重构会让基线
失效，已直接删除。

## Consequences

若有人质疑某条错误 `detail` 的译法，或要给新业务模块沿用同一套中文措辞规则，去归档目录查
`translate_tools/translate_msgs.py`。同目录还有 `translate_msgs_round2.py` 和
`round3.py` 两轮增补，以及 `extract_msgs.py` / `extract_plain.py` / `extract_fstrings.py`
三个抽取脚本。

**这些脚本在归档目录里直接跑会静默失效。** 它们用 `ROOT = Path(__file__).parent` 加
`TARGETS = [ROOT/"src", ROOT/"tests"]`，期望自己躺在 `backend/` 下。在归档目录里跑会处理
0 个文件且不报错。要用必须先改 `ROOT`，或把脚本放回 `backend/`。

`.gitignore` 里 `output/` 与 `.playwright-cli/` 两条规则保留着：
`.codex/skills/playwright/` 把 `output/playwright/` 约定为本仓库的产物目录，下次 UI 走查
会自行重建。
