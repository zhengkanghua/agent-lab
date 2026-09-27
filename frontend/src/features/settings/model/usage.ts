import type { UsageFilters } from '@/api/usage'

/**
 * 用量分区里与展示有关的纯函数：日期换算、空筛选与数字格式化。
 *
 * **时区换算只发生在这里。** 用户选的是本地日期，后端只认 UTC 时刻，所以日期控件的值在这里
 * 换算成 UTC 的 ISO 字符串再发出去；反方向（展示某条记录的时刻）也在这里换算回本地。把这个
 * 换算写在组件里会让它散落在多个用到日期的地方，而写错它会表现为「某一天的数据少了几个小时」
 * 这种很难归因的偏差。
 */

/** 空筛选：三维都不筛。 */
export function emptyUsageFilters(): UsageFilters {
  return { model: '', start: '', end: '' }
}

/**
 * 本地日期（`YYYY-MM-DD`）→ 当天本地零点对应的 UTC 时刻。
 *
 * 不要用 `new Date(date)`：那样得到的是 UTC 零点，在中国时区会少掉凌晨那 8 小时。
 */
export function localDayStartIso(date: string): string {
  if (date === '') return ''
  return new Date(`${date}T00:00:00`).toISOString()
}

/**
 * 本地日期 → **次日**本地零点对应的 UTC 时刻。
 *
 * 后端的区间是闭开的（起点含、终点不含），而用户理解的「筛到 3 月 5 日」包含 5 日一整天，
 * 所以这里给的是 6 日零点。直接用 5 日零点会静默丢掉 5 日当天的全部调用。
 */
export function localDayEndExclusiveIso(date: string): string {
  if (date === '') return ''
  const next = new Date(`${date}T00:00:00`)
  next.setDate(next.getDate() + 1)
  return next.toISOString()
}

/** token 数的展示：null（上游没报过）与 0 必须看起来不一样。 */
export function formatTokens(value: number | null): string {
  if (value === null) return '—'
  return value.toLocaleString('zh-CN')
}

/** 耗时展示：一秒以内按毫秒，超过按秒并保留一位小数。 */
export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(1)} s`
}

/** 发生时刻按浏览器时区展示，精确到秒。 */
export function formatOccurredAt(isoTimestamp: string): string {
  const at = new Date(isoTimestamp)
  if (Number.isNaN(at.getTime())) return isoTimestamp
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`
  )
}
