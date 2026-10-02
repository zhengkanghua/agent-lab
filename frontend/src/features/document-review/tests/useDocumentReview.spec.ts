import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { draftId, reviewDetail, reviewDocumentId } from '@/api/document-review.fixture'

/* 审核工作台的状态机（396 行、零直接测试之前）。这里只盯住几条「错了会静默毁数据或
 * 静默卡住」的行为：
 *   - 后台轮询绝不能覆盖用户尚未保存的正文；
 *   - 服务端修订前进而本地有改动时必须显式标记冲突，不许静默采用任何一边；
 *   - 冲突未决时命令一律挡住，只有用户明确选择的那条路（allowConflict）能过；
 *   - 读取失败后进「待核实」，命令不再自动重放（超时不重放是后端的既定契约）；
 *   - 迟到的读取不能覆盖后发起的那次结果（序号守卫）；
 *   - 只有处理中的状态才排轮询，进入终态要停下。
 */

const api = vi.hoisted(() => ({
  getDocumentReview: vi.fn(),
  startDocumentReview: vi.fn(),
  saveDocumentDraft: vi.fn(),
  previewDocumentDraft: vi.fn(),
  adoptDocumentCandidate: vi.fn(),
  rejectDocumentCandidate: vi.fn(),
  retryDocumentCandidate: vi.fn(),
  useLatestDocumentSource: vi.fn(),
  deleteManagedDocument: vi.fn(),
}))

vi.mock('@/api/document-review', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  ...api,
}))

vi.mock('@/shared/composables/confirm', () => ({ requestConfirm: vi.fn() }))

import { useDocumentReview } from '../useDocumentReview'

function receipt() {
  return {
    document_id: reviewDocumentId,
    processing_id: draftId,
    candidate_revision: 3,
    state: 'review',
  }
}

/** 挂进一个 effect scope：useDocumentReview 用 onScopeDispose 收尾（停轮询、断请求），
 *  不放进 scope 就会跨用例留下一个 5 秒的定时器。 */
function mountReview() {
  const scope = effectScope()
  const review = scope.run(() => useDocumentReview(reviewDocumentId))!
  return { review, stop: () => scope.stop() }
}

/** 让 watch(immediate) 触发的那次读取跑完。假定时器下不能等 flushPromises
 *  （它内部是 setTimeout 0），改用推进 1ms。 */
const settle = () => vi.advanceTimersByTimeAsync(1)

describe('useDocumentReview', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    api.getDocumentReview.mockReset().mockResolvedValue(reviewDetail())
    api.startDocumentReview.mockReset().mockResolvedValue(receipt())
    api.saveDocumentDraft.mockReset().mockResolvedValue(receipt())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('后台轮询刷新不覆盖尚未保存的正文', async () => {
    const { review, stop } = mountReview()
    await settle()

    review.title.value = '本地标题'
    review.text.value = '本地正文'
    expect(review.dirty.value).toBe(true)

    // 同一次修订再读一遍：别人没动过，本地那份就该原样留着。
    await review.refresh()

    expect(review.title.value).toBe('本地标题')
    expect(review.text.value).toBe('本地正文')
    expect(review.dirty.value).toBe(true)
    expect(review.conflict.value).toBe(false)
    stop()
  })

  it('服务端修订前进了而本地有改动：标记冲突，不静默采用任何一边', async () => {
    const { review, stop } = mountReview()
    await settle()
    review.text.value = '本地正文'

    const moved = reviewDetail()
    moved.document.management_revision = 11
    api.getDocumentReview.mockResolvedValue(moved)
    await review.refresh()

    expect(review.conflict.value).toBe(true)
    // 冲突时不覆盖本地：用户手上那份还在，由他决定留哪个。
    expect(review.text.value).toBe('本地正文')
    stop()
  })

  it('冲突后命令被挡住，只有用户明确选择的那条路能过', async () => {
    const { review, stop } = mountReview()
    await settle()
    review.text.value = '本地正文'
    const moved = reviewDetail()
    moved.document.management_revision = 11
    api.getDocumentReview.mockResolvedValue(moved)
    await review.refresh()
    expect(review.conflict.value).toBe(true)

    expect(await review.save()).toBe(false)
    expect(api.saveDocumentDraft).not.toHaveBeenCalled()
    expect(await review.start()).toBe(false)
    expect(api.startDocumentReview).not.toHaveBeenCalled()

    // keepLocalEdits 走 allowConflict：换用最新服务端草稿，但保留用户自己的正文。
    await review.keepLocalEdits()
    expect(api.startDocumentReview).toHaveBeenCalledWith(reviewDocumentId, 11, draftId)
    expect(review.text.value).toBe('本地正文')
    stop()
  })

  it('读取失败后进入待核实，命令不再自动重放', async () => {
    const { review, stop } = mountReview()
    await settle()

    api.getDocumentReview.mockRejectedValue(
      new ApiError({ message: '网络错误', code: 'network_error' }),
    )
    expect(await review.refresh()).toBe(false)

    expect(review.stale.value).toBe(true)
    expect(review.error.value).toBeTruthy()
    expect(await review.start()).toBe(false)
    expect(api.startDocumentReview).not.toHaveBeenCalled()
    stop()
  })

  it('命令成功后换用服务端那份，并推进历史修订号', async () => {
    const { review, stop } = mountReview()
    await settle()
    const before = review.historyRevision.value

    expect(await review.start()).toBe(true)

    expect(api.startDocumentReview).toHaveBeenCalledWith(reviewDocumentId, 10, draftId)
    expect(review.historyRevision.value).toBe(before + 1)
    expect(review.feedback.value).toBeTruthy()
    stop()
  })

  it('迟到的读取不会覆盖后发起的那次结果', async () => {
    const { review, stop } = mountReview()
    await settle()

    let resolveStale!: (value: unknown) => void
    api.getDocumentReview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStale = resolve
        }),
    )
    const staleRead = review.refresh()
    const freshRead = review.refresh()
    await freshRead

    // 旧那次现在才回来，带着一个已经过期的修订号。
    const late = reviewDetail()
    late.document.management_revision = 7
    resolveStale(late)
    await staleRead

    expect(review.detail.value?.document.management_revision).toBe(10)
    stop()
  })

  it('候选在处理中时按 5 秒轮询，进入终态后停下', async () => {
    const processing = reviewDetail()
    processing.candidate.state = 'indexing'
    api.getDocumentReview.mockResolvedValue(processing)

    const { stop } = mountReview()
    await settle()
    const reads = api.getDocumentReview.mock.calls.length

    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getDocumentReview.mock.calls.length).toBe(reads + 1)

    // 回到终态（待审核）后不该再排下一轮。
    const terminal = reviewDetail()
    api.getDocumentReview.mockResolvedValue(terminal)
    await vi.advanceTimersByTimeAsync(5000)
    const settled = api.getDocumentReview.mock.calls.length
    await vi.advanceTimersByTimeAsync(30000)
    expect(api.getDocumentReview.mock.calls.length).toBe(settled)
    stop()
  })
})
