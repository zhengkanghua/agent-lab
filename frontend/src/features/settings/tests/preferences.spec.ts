// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { DEFAULT_PREFERENCES, sanitizePreferences } from '../model/preferences'

describe('preferences 模型', () => {
  it('默认值与后端契约的中位档一致', () => {
    expect(DEFAULT_PREFERENCES).toEqual({
      documentLimit: 10,
      matchesPerDocument: 3,
    })
  })

  it('非法输入整体落回默认，不让设置页打不开', () => {
    expect(sanitizePreferences(undefined)).toEqual(DEFAULT_PREFERENCES)
    expect(sanitizePreferences(null)).toEqual(DEFAULT_PREFERENCES)
    expect(sanitizePreferences('垃圾')).toEqual(DEFAULT_PREFERENCES)
    expect(sanitizePreferences({ documentLimit: '很多' })).toMatchObject({
      documentLimit: DEFAULT_PREFERENCES.documentLimit,
    })
  })

  it('数量参数按契约边界归一，坏值不外发', () => {
    expect(sanitizePreferences({ documentLimit: 0 }).documentLimit).toBe(1)
    expect(sanitizePreferences({ documentLimit: 9999 }).documentLimit).toBe(100)
    expect(sanitizePreferences({ documentLimit: 7.9 }).documentLimit).toBe(7)
    expect(sanitizePreferences({ matchesPerDocument: -1 }).matchesPerDocument).toBe(1)
  })
})
