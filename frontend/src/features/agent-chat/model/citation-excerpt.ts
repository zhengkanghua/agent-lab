import { CITATION_MARKER_PATTERN, citationMarkerRegExp } from '@/shared/model/citation-marker'
import type { AgentToolTrace } from './conversation'

/* 引用标记（`[[E0123456789ab]]`）的唯一定义在 `shared/model/citation-marker.ts`：
 * 渲染答案正文的 `SafeMarkdown.vue`（shared）与这里（证据面板的切块）共用同一份模式。
 * 两处一旦漂移，表现是点了小丸却静默回落成「打开当前全文」，而且不会有任何报错。
 *
 * 这里转出来，是为了让本 feature 只有一个人口（面板与切块都从本文件取），
 * 而不是各处记着要去 shared 拿。
 */
export { CITATION_MARKER_PATTERN, citationMarkerRegExp }

/* 工具正文里「正文从这里开始」的两个标记。
 *
 * 它们与后端的拼法是同一份契约，改动必须两边一起改：
 *   - `backend/src/agent_lab/agent/tools/search_documents.py`（每个命中一篇一块，
 *     块头之后跟 `实际命中片段:`）
 *   - `backend/src/agent_lab/agent/tools/read_document.py`（`当前正文:`）
 * 漂移的后果同样是静默回落成打开当前全文，所以这里单独成常量，不散落在解析代码里。 */
export const EVIDENCE_BODY_MARKERS = ['实际命中片段:', '当前正文:'] as const

/** 工具正文里一个块的开始：行首的引用标记。块到下一个行首标记或正文末尾为止。 */
const BLOCK_MARKER_LINE = new RegExp(`^${CITATION_MARKER_PATTERN}`)

interface EvidenceBlock {
  citationId: string
  /** 从标记行开始、到下一块之前（或正文末尾）为止的整块文本。 */
  text: string
}

/* 按行首标记切块。
 *
 * **不能假设一份工具正文只有一个块**：一次 `search_documents` 命中多篇时，每个命中一篇拼一块，
 * 全在同一份正文里。只认第一块的话，点第 2 篇的引用会拿到第 1 篇的正文——那是错的答案，
 * 比切不出来更糟（切不出来至少会回落到打开全文）。 */
function blocksOf(content: string): EvidenceBlock[] {
  const blocks: EvidenceBlock[] = []
  let current: { citationId: string; lines: string[] } | null = null
  for (const line of content.split('\n')) {
    const marker = line.match(BLOCK_MARKER_LINE)
    if (marker) {
      if (current) blocks.push({ citationId: current.citationId, text: current.lines.join('\n') })
      current = { citationId: marker[1]!, lines: [line] }
      continue
    }
    current?.lines.push(line)
  }
  if (current) blocks.push({ citationId: current.citationId, text: current.lines.join('\n') })
  return blocks
}

/* 块内第一个正文标记之后的部分。
 *
 * 取「最早出现的那个标记」而不是按 EVIDENCE_BODY_MARKERS 的顺序试：一份正文里正常只会命中
 * 其中一个，但按位置取才不会因为将来的拼法变化把整块元数据当成正文交出去。 */
function bodyOf(blockText: string): string {
  let start = -1
  let length = 0
  for (const marker of EVIDENCE_BODY_MARKERS) {
    const index = blockText.indexOf(marker)
    if (index === -1) continue
    if (start === -1 || index < start) {
      start = index
      length = marker.length
    }
  }
  if (start === -1) return ''
  return blockText.slice(start + length).trim()
}

/** 面板主体：当初那段正文，以及它是从哪条工具轨迹里切出来的。 */
export interface CitationExcerpt {
  body: string
  traceId: string
}

/**
 * 在这一轮的 Tool 结果里找出这条证据引用对应的那段正文。
 *
 * 引用本身只带文档身份（`DocumentEvidence` 没有正文），正文一直在这轮的工具轨迹里，所以按
 * 后端拼工具正文的标记认回去，而不是另存一份：另存会多一份会漂移的副本。
 *
 * 三种情况返回 `null`，调用方据此回落到「打开当前全文」（不展开面板、不加解释文案）：
 * 这一轮的轨迹里没有含该标记的（例如工具结果没送达、正文被换成了收尾提示语）、
 * 有标记但切不出这一块、块里取不到主体。
 *
 * 主体里可能含历史清理留下的「（中间部分已省略）」——那是**如实告诉用户这段被截过**，
 * 照原样交出去，不能因为含占位文字就降级：降级去打开当前全文正是这个面板要避免的事。
 */
export function excerptFor(
  citationId: string,
  traces: readonly AgentToolTrace[],
): CitationExcerpt | null {
  const marker = `[[${citationId}]]`
  for (const trace of traces) {
    if (!trace.content?.includes(marker)) continue
    const block = blocksOf(trace.content).find((item) => item.citationId === citationId)
    if (!block) return null
    const body = bodyOf(block.text)
    if (!body) return null
    return { body, traceId: trace.id }
  }
  return null
}
