import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fetchPreferences: vi.fn(),
  savePreferences: vi.fn(),
}))

vi.mock('@/api/preferences', () => api)

import { ApiError } from '@/api/client'
import { DEFAULT_PREFERENCES } from '../model/preferences'
import { usePreferences } from '../composables/usePreferences'

/** 后端那一份的形状（字段名与前端不同）。 */
function remote(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    systemPrompt: '',
    documentLimit: 20,
    matchesPerDocument: 5,
    ...overrides,
  }
}

describe('usePreferences（应用级单例 + 后端读写）', () => {
  beforeEach(() => {
    api.fetchPreferences.mockReset()
    api.savePreferences.mockReset()
    usePreferences().resetForTests()
  })

  it('两次调用拿到同一份状态：检索页读到的就是设置页写下的', () => {
    const first = usePreferences()
    const second = usePreferences()

    first.preferences.documentLimit = 20

    expect(second.preferences.documentLimit).toBe(20)
  })

  it('加载把后端那份写进 store，并转成前端的字段名', async () => {
    api.fetchPreferences.mockResolvedValue(
      remote({ systemPrompt: '你是财经记者。', documentLimit: 20, matchesPerDocument: 5 }),
    )
    const { preferences, load, loadState } = usePreferences()

    await load()

    expect(preferences.documentLimit).toBe(20)
    expect(preferences.matchesPerDocument).toBe(5)
    expect(preferences.agentSystemPrompt).toBe('你是财经记者。')
    expect(loadState.value).toBe('ready')
  })

  it('读接口失败时落回契约默认值，状态标失败但不抛', async () => {
    // 偏好是自身操作默认值，读不到不影响任何数据的正确性；把它变成一次可见失败
    // 只会让设置页看起来坏了。
    api.fetchPreferences.mockRejectedValue(new ApiError({ message: 'nope', code: 'network_error' }))
    const { preferences, load, loadState } = usePreferences()
    preferences.documentLimit = 99

    await expect(load()).resolves.toBeUndefined()

    expect(preferences.documentLimit).toBe(DEFAULT_PREFERENCES.documentLimit)
    expect(loadState.value).toBe('failed')
  })

  it('并发的加载复用同一次请求，不各发一条', async () => {
    api.fetchPreferences.mockResolvedValue(remote())
    const { load } = usePreferences()

    await Promise.all([load(), load(), load()])

    expect(api.fetchPreferences).toHaveBeenCalledTimes(1)
  })

  it('读成功之前不允许编辑，也不允许保存', async () => {
    // 写入是整体覆盖，提交的是 `{...preferences}` 整份。读还没成功时 store 里是默认值，
    // 此时放行一次保存，就会把账号上真实存着的那份（可能是自定义提示词）覆盖成默认——
    // 用户只是想改个下拉，提示词却没了。
    const { canEdit, save, load } = usePreferences()

    expect(canEdit.value).toBe(false)
    await expect(
      save({ documentLimit: 20, matchesPerDocument: 5, agentSystemPrompt: '你是记者。' }),
    ).rejects.toBeInstanceOf(ApiError)
    // 被拒绝的那次不能真的发出去。
    expect(api.savePreferences).not.toHaveBeenCalled()

    api.fetchPreferences.mockResolvedValue(remote())
    await load()
    expect(canEdit.value).toBe(true)
  })

  it('读接口失败后仍不可编辑，避免拿默认值把服务端配置覆盖掉', async () => {
    api.fetchPreferences.mockRejectedValue(new ApiError({ message: 'nope', code: 'network_error' }))
    const { canEdit, save, load } = usePreferences()

    await load()

    expect(canEdit.value).toBe(false)
    await expect(
      save({ documentLimit: 10, matchesPerDocument: 3, agentSystemPrompt: '' }),
    ).rejects.toBeInstanceOf(ApiError)
    expect(api.savePreferences).not.toHaveBeenCalled()
  })

  it('保存成功后用服务端回读的那份覆盖本地', async () => {
    api.fetchPreferences.mockResolvedValue(remote())
    const { load } = usePreferences()
    await load()

    // 服务端会把空白提示词归一成「未配置」。界面要显示服务端那份，否则用户会看到
    // 一个「保存了但显示的还是刚才那串空格」的假象。
    api.savePreferences.mockResolvedValue(remote({ systemPrompt: '', documentLimit: 10 }))
    const { preferences, save } = usePreferences()

    await save({ documentLimit: 10, matchesPerDocument: 3, agentSystemPrompt: '   ' })

    expect(preferences.agentSystemPrompt).toBe('')
    expect(preferences.documentLimit).toBe(10)
  })

  it('保存失败时抛出，本地值不动', async () => {
    api.fetchPreferences.mockResolvedValue(remote())
    const { preferences, save, load } = usePreferences()
    await load()

    api.savePreferences.mockRejectedValue(new ApiError({ message: 'nope', code: 'network_error' }))
    preferences.documentLimit = 20

    await expect(
      save({ documentLimit: 20, matchesPerDocument: 3, agentSystemPrompt: '' }),
    ).rejects.toBeInstanceOf(ApiError)

    expect(preferences.documentLimit).toBe(20)
  })

  it('提交时把前端偏好转成接口要的形状', async () => {
    api.fetchPreferences.mockResolvedValue(remote())
    const { save, load } = usePreferences()
    await load()

    api.savePreferences.mockResolvedValue(remote())

    await save({ documentLimit: 5, matchesPerDocument: 3, agentSystemPrompt: '你是记者。' })

    expect(api.savePreferences).toHaveBeenCalledWith(
      { systemPrompt: '你是记者。', documentLimit: 5, matchesPerDocument: 3 },
      { notifyUnauthorized: false },
    )
  })
})
