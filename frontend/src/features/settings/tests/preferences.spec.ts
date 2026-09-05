import { describe, expect, it } from 'vitest'
import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'
import {
  DEFAULT_PREFERENCES,
  PREFERENCES_STORAGE_KEY,
  sanitizePreferences,
  validateAgentSystemPrompt,
} from '../model/preferences'

describe('preferences 模型', () => {
  it('默认值与后端契约的中位档一致', () => {
    expect(DEFAULT_PREFERENCES).toEqual({
      documentLimit: 10,
      matchesPerDocument: 3,
      agentSystemPrompt: '',
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

  it('超长提示词截断到契约上界，不抛错', () => {
    const long = 'x'.repeat(MAX_SYSTEM_PROMPT_CHARACTERS + 500)
    expect(sanitizePreferences({ agentSystemPrompt: long }).agentSystemPrompt).toHaveLength(
      MAX_SYSTEM_PROMPT_CHARACTERS,
    )
  })

  it('提示词校验：空白合法（语义是回到服务端默认），超长报错', () => {
    expect(validateAgentSystemPrompt('')).toBeNull()
    expect(validateAgentSystemPrompt('  \n ')).toBeNull()
    expect(validateAgentSystemPrompt('x'.repeat(MAX_SYSTEM_PROMPT_CHARACTERS))).toBeNull()
    expect(validateAgentSystemPrompt('x'.repeat(MAX_SYSTEM_PROMPT_CHARACTERS + 1))).toContain(
      '不能超过',
    )
  })

  it('存储键带版本号，键名改了旧数据不会被误读', () => {
    expect(PREFERENCES_STORAGE_KEY).toBe('signaldesk.preferences.v1')
  })
})
