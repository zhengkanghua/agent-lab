import { describe, expect, it } from 'vitest'
import {
  emptyUsageFilters,
  formatDuration,
  formatOccurredAt,
  formatTokens,
  localDayEndExclusiveIso,
  localDayStartIso,
} from '../model/usage'

/**
 * 日期换算与数字格式化。
 *
 * 这些函数决定「筛 3 月 1 日到 3 月 5 日」到底筛到哪些调用，写错的表现是数据少了几个小时或
 * 多了一天——很容易被当成后端的问题，所以在纯函数层把它们钉住。
 */
describe('用量分区的展示换算', () => {
  it('起始日期取当天本地零点，而不是 UTC 零点', () => {
    // new Date('2026-03-01') 拿到的是 UTC 零点，在东八区会少掉当天凌晨 8 小时。
    expect(localDayStartIso('2026-03-01')).toBe(new Date('2026-03-01T00:00:00').toISOString())
  })

  it('结束日期取次日零点：后端区间闭开，用户说的「到 5 日」包含 5 日整天', () => {
    expect(localDayEndExclusiveIso('2026-03-05')).toBe(
      new Date('2026-03-06T00:00:00').toISOString(),
    )
  })

  it('空日期不产生时刻，让后端当「不限」处理', () => {
    expect(localDayStartIso('')).toBe('')
    expect(localDayEndExclusiveIso('')).toBe('')
  })

  it('空筛选三维都不筛', () => {
    expect(emptyUsageFilters()).toEqual({ model: '', start: '', end: '' })
  })

  it('缺失的 token 显示成破折号，与 0 分开', () => {
    expect(formatTokens(null)).toBe('—')
    expect(formatTokens(0)).toBe('0')
  })

  it('耗时一秒以内按毫秒、超过按秒', () => {
    expect(formatDuration(999)).toBe('999 ms')
    expect(formatDuration(1500)).toBe('1.5 s')
  })

  it('发生时刻按本地时区展示，非法值原样回显', () => {
    const iso = new Date('2026-03-01T04:05:06').toISOString()
    expect(formatOccurredAt(iso)).toBe('2026-03-01 04:05:06')
    expect(formatOccurredAt('not-a-time')).toBe('not-a-time')
  })
})
