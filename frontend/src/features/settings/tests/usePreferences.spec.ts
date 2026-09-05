import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_PREFERENCES, PREFERENCES_STORAGE_KEY } from '../model/preferences'
import { usePreferences } from '../composables/usePreferences'

describe('usePreferences（应用级单例 + localStorage 持久化）', () => {
  beforeEach(() => {
    localStorage.clear()
    // 偏好是模块级单例，用例间手工复位，避免顺序相关的假失败。
    Object.assign(usePreferences().preferences, DEFAULT_PREFERENCES)
  })

  it('两次调用拿到同一份状态：检索页读到的就是设置页写下的', () => {
    const first = usePreferences()
    const second = usePreferences()

    first.preferences.documentLimit = 20

    expect(second.preferences.documentLimit).toBe(20)
  })

  it('修改立即落盘', async () => {
    const { preferences } = usePreferences()
    preferences.documentLimit = 5
    preferences.agentSystemPrompt = '你是财经记者。'

    const persisted = JSON.parse(localStorage.getItem(PREFERENCES_STORAGE_KEY) ?? '{}')

    expect(persisted).toMatchObject({ documentLimit: 5, agentSystemPrompt: '你是财经记者。' })
  })

  it('恢复默认只重置数量参数，不动提示词', () => {
    const { preferences, resetSearchPreferences } = usePreferences()
    preferences.documentLimit = 20
    preferences.agentSystemPrompt = '你是财经记者。'

    resetSearchPreferences()

    expect(preferences.documentLimit).toBe(DEFAULT_PREFERENCES.documentLimit)
    expect(preferences.agentSystemPrompt).toBe('你是财经记者。')
    // 落盘内容同样只有数量参数回到默认。
    const persisted = JSON.parse(localStorage.getItem(PREFERENCES_STORAGE_KEY) ?? '{}')
    expect(persisted.documentLimit).toBe(DEFAULT_PREFERENCES.documentLimit)
  })
})
