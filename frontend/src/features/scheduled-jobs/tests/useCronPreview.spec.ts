import { effectScope, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'

const api = vi.hoisted(() => ({
  validateCron: vi.fn(),
}))

vi.mock('@/api/scheduled-jobs', () => api)

import { useCronPreview } from '../composables/useCronPreview'

describe('useCronPreview', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    api.validateCron.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('debounces input and shows the next three runs in Beijing time on success', async () => {
    api.validateCron.mockResolvedValue({
      next_run_times: ['2026-09-03T01:00:00Z', '2026-09-04T01:00:00Z', '2026-09-05T01:00:00Z'],
      next_run_times_local: [
        '2026-09-03T09:00:00+08:00',
        '2026-09-04T09:00:00+08:00',
        '2026-09-05T09:00:00+08:00',
      ],
    })

    const cron = ref('*/10 * * * *')
    const scope = effectScope()
    const preview = scope.run(() => useCronPreview(cron))!

    // 形状合法 → 进入 checking，但防抖 300ms 内不发请求。
    expect(preview.state.value).toBe('checking')
    expect(api.validateCron).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(300)
    expect(api.validateCron).toHaveBeenCalledTimes(1)
    await flushPromises()

    expect(preview.state.value).toBe('valid')
    expect(preview.canSubmit.value).toBe(true)
    expect(preview.previewTimes.value).toEqual([
      '2026-09-03 09:00:00',
      '2026-09-04 09:00:00',
      '2026-09-05 09:00:00',
    ])
    scope.stop()
  })

  it('does not call the API when the 5-field shape is wrong', async () => {
    const cron = ref('bad cron')
    const scope = effectScope()
    const preview = scope.run(() => useCronPreview(cron))!
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(api.validateCron).not.toHaveBeenCalled()
    expect(preview.state.value).toBe('invalid')
    expect(preview.canSubmit.value).toBe(false)
    scope.stop()
  })

  it('marks the preview invalid when the server rejects the expression', async () => {
    // 必须是真 ApiError：resolveErrorCopy 对非 ApiError 一律走兜底，拿不到 code 文案。
    api.validateCron.mockRejectedValue(
      new ApiError({
        message: 'cron 表达式无效，需要 5 段式 cron（分 时 日 月 周）。',
        status: 422,
        code: 'scheduled_job_invalid_cron',
      }),
    )

    const cron = ref('99 * * * *')
    const scope = effectScope()
    const preview = scope.run(() => useCronPreview(cron))!
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(preview.state.value).toBe('invalid')
    expect(preview.canSubmit.value).toBe(false)
    expect(preview.message.value).toBe('cron 表达式无效，需要 5 段式 cron（分 时 日 月 周）。')
    scope.stop()
  })
})
