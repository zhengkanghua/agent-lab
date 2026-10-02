import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime } from '../datetime'

/* 时间口径的核验。
 *
 * 重点不是「能格式化」，而是「不管跑在哪个时区都一样」——这正是这组函数存在的理由：
 * 之前 6 处行内调用跟随浏览器时区，同一条记录在不同机器上会显示成不同的时刻。
 */

describe('formatDateTime', () => {
  it('输出 YYYY-MM-DD HH:mm:ss，用短横线而不是 zh-CN 默认的斜杠', () => {
    expect(formatDateTime('2026-09-08T00:00:00Z')).toBe('2026-09-08 08:00:00')
  })

  it('固定按东八区换算，与运行机器的时区无关', () => {
    // 同一时刻：UTC 的 00:00 就是东八区的 08:00。
    expect(formatDateTime('2026-09-08T00:00:00Z')).toBe('2026-09-08 08:00:00')
    // 跨日边界：UTC 的前一天 16:00 已经是东八区的次日 00:00。
    expect(formatDateTime('2026-09-07T16:00:00Z')).toBe('2026-09-08 00:00:00')
  })

  it('带偏移量的输入换算到东八区', () => {
    expect(formatDateTime('2026-09-08T12:00:00+09:00')).toBe('2026-09-08 11:00:00')
  })

  it('缺值给破折号', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
    expect(formatDateTime('')).toBe('—')
  })

  it('解析不了的输入原样返回，让异常数据在界面上看得见', () => {
    expect(formatDateTime('不是时间')).toBe('不是时间')
  })
})

describe('formatDate', () => {
  it('只到日，同样固定东八区', () => {
    expect(formatDate('2026-09-07T16:00:00Z')).toBe('2026-09-08')
  })

  it('缺值与异常输入的处理与 formatDateTime 一致', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('坏数据')).toBe('坏数据')
  })
})
