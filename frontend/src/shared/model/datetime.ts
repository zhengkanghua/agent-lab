/* 后端时间戳的展示格式。
 *
 * 固定东八区（Asia/Shanghai），不跟浏览器本地时区走：这是 shared/model/readable-result.ts
 * 早就定下的口径——同一条记录在不同机器上必须显示同一个时刻，否则「这是哪天更新的」在不同
 * 同事那里会得到不同答案。2026-10 之前有 6 处行内 `new Date(x).toLocaleString('zh-CN')`，
 * 它们既跟随机器时区、又输出斜杠形状（2026/09/03），与这条口径正好相反。
 *
 * 形状是 `YYYY-MM-DD HH:mm:ss` 与 `YYYY-MM-DD`：用 formatToParts 自己拼而不是直接调
 * formatter.format，因为 zh-CN 的 format 输出带斜杠，不是这里要的形状。
 *
 * 两处刻意不走这里：
 *   - features/settings/model/usage.ts 的用量时刻按浏览器本地时区显示——它回答的是
 *     「我自己什么时候用的」，用户在自己的钟表上核对更顺；
 *   - shared/model/readable-result.ts 的发布时间只到日、且要区分「时间未提供」，
 *     那是检索结果卡的既有形状。
 */

const DATE_TIME_PARTS = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const DATE_PARTS = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function partsOf(formatter: Intl.DateTimeFormat, value: string): Record<string, string> {
  const parts = formatter.formatToParts(new Date(value))
  const collected: Record<string, string> = {}
  for (const part of parts) collected[part.type] = part.value
  return collected
}

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * `YYYY-MM-DD HH:mm:ss`（东八区）。
 *
 * 缺值给 `—`；解析不了的输入**原样返回**，让异常数据在界面上看得见，
 * 而不是悄悄显示成 Invalid Date 或一个空串。
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parse(value)
  if (!date) return value

  const parts = partsOf(DATE_TIME_PARTS, value)
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

/** `YYYY-MM-DD`（东八区）。缺值与异常输入的处理同 formatDateTime。 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parse(value)
  if (!date) return value

  const parts = partsOf(DATE_PARTS, value)
  return `${parts.year}-${parts.month}-${parts.day}`
}
