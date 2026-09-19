/**
 * 领域模型文档的结构门禁（pi 侧）。
 *
 * 拦住对 `CONTEXT.md` 与 `docs/adr/**` 的写入，校验内容是否符合
 * `.codex/skills/domain-modeling/FORMAT-CONTRACT.md`。规则不在这里——本扩展只是把
 * `scripts/check-domain-docs.mjs` 挂到 pi 的工具调用事件上；规则改了，这里不用动。
 *
 * 为什么拦结构而不是拦「有没有调用过 skill」：调用记录拦不住真正的失败模式——模型可以不调
 * skill 而写出一份格式对、判据错的 ADR。写出来的东西合不合规是确定的，可以机械判断。
 *
 * 拒绝时把违规规则和 skill 路径一起回给模型，让它知道该去读什么，而不是纳闷为什么被拦。
 *
 * 已知边界：本扩展只覆盖 write / edit 工具。bash 里的 `cat > docs/adr/x.md`、`sed -i`、
 * `python -c` 都能绕过它——任何 write/edit 闸门都是如此，Codex 官方文档也写明 hook 是护栏
 * 而非完整执行边界。落盘后的兜底是 `.githooks/pre-commit` 与手动跑校验脚本。
 */

import { spawnSync } from 'node:child_process';
import { join } from 'node:path';

import { isToolCallEventType, type ExtensionAPI } from '@earendil-works/pi-coding-agent';

const SCRIPT = 'scripts/check-domain-docs.mjs';

/** 本门禁管辖的路径：术语表与 ADR 目录。与契约里的 dir/file 保持一致。 */
function isGuardedPath(path: unknown): boolean {
  if (typeof path !== 'string') return false;
  const normalized = path.replace(/\\/g, '/');
  if (normalized === 'CONTEXT.md' || normalized.endsWith('/CONTEXT.md')) return true;
  return /(^|\/)docs\/adr\/[^/]+\.md$/.test(normalized);
}

/**
 * 把工具调用交给校验脚本，让它决定放行还是拦截。
 *
 * 输出契约只有一条：脚本违规时退出 2、把理由写 stderr。这里直接把 stderr 作为 block 的 reason
 * ——模型看不到理由就只会盲试，而那正是这套门禁要避免的。
 */
function guard(cwd: string, payload: Record<string, unknown>): { block: true; reason: string } | undefined {
  const result = spawnSync(process.execPath, [join(cwd, SCRIPT), '--stdin'], {
    cwd,
    input: JSON.stringify(payload),
    encoding: 'utf8',
  });

  if (result.status !== 2) return undefined;
  const reason = (result.stderr || result.stdout || '').trim();
  return {
    block: true,
    reason: reason || '内容不符合领域模型文档格式契约，请先读 .codex/skills/domain-modeling/。',
  };
}

export default function (pi: ExtensionAPI) {
  pi.on('tool_call', async (event, ctx) => {
    if (isToolCallEventType('write', event)) {
      const input = event.input as { path?: string; content?: string };
      if (!isGuardedPath(input.path)) return;
      return guard(ctx.cwd, { path: input.path, content: input.content });
    }

    if (isToolCallEventType('edit', event)) {
      const input = event.input as {
        path?: string;
        edits?: Array<{ oldText: string; newText: string }>;
      };
      if (!isGuardedPath(input.path)) return;
      return guard(ctx.cwd, { path: input.path, edits: input.edits });
    }
  });
}
