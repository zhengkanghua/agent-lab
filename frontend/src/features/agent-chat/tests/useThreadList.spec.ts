import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ApiError } from '@/api/client'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'

const api = vi.hoisted(() => ({
  listAgentThreads: vi.fn(),
  deleteAgentThread: vi.fn(),
  getAgentThreadMessages: vi.fn(),
}))

vi.mock('@/api/agent-threads', () => api)

import { THREAD_PAGE_SIZE, useThreadList } from '../composables/useThreadList'

function thread(index: number, overrides: Partial<AgentThreadSummaryDto> = {}) {
  return {
    thread_id: `30000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    title: `会话 ${index}`,
    created_at: '2026-08-18T00:00:00Z',
    last_active_at: '2026-08-18T01:00:00Z',
    ...overrides,
  }
}

function page(count: number, total = count) {
  return {
    items: Array.from({ length: count }, (_unused, index) => thread(index + 1)),
    total,
  }
}

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'

/** 在 effectScope 内构造，让 onScopeDispose 有地方挂，并通过 App 注入 Vue Query。 */
function build(options: Partial<Parameters<typeof useThreadList>[0]> = {}) {
  const onActiveThreadDeleted = options.onActiveThreadDeleted ?? vi.fn()
  const activeThreadId = options.activeThreadId ?? (() => null)
  
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  
  let result: ReturnType<typeof useThreadList> | undefined

  const app = createApp({
    setup() {
      result = useThreadList({ onActiveThreadDeleted, activeThreadId })
      return () => null
    }
  })
  app.use(VueQueryPlugin, { queryClient })
  
  const container = document.createElement('div')
  app.mount(container)
  
  return { 
    list: result!, 
    scope: { stop: () => app.unmount() }, 
    onActiveThreadDeleted,
    queryClient
  }
}

describe('useThreadList', () => {
  beforeEach(() => {
    api.listAgentThreads.mockReset()
    api.deleteAgentThread.mockReset()
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('载入第一页并记下总数', async () => {
    api.listAgentThreads.mockResolvedValue(page(3, 7))
    const { list, scope } = build()

    await flushPromises()

    expect(list.threads.value).toHaveLength(3)
    expect(list.total.value).toBe(7)
    expect(list.listState.value).toBe('ready')
    expect(list.hasMore.value).toBe(true)
    expect(list.hasPrevious.value).toBe(false)
    scope.stop()
  })

  it('翻页只改 offset，limit 始终是约定的页大小', async () => {
    api.listAgentThreads.mockResolvedValue(page(THREAD_PAGE_SIZE, 60))
    const { list, scope } = build()
    await flushPromises()

    await list.nextPage()

    expect(api.listAgentThreads).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: THREAD_PAGE_SIZE, offset: THREAD_PAGE_SIZE }),
    )
    expect(list.hasPrevious.value).toBe(true)
    scope.stop()
  })

  it('已在最后一页时「下一页」不发请求', async () => {
    api.listAgentThreads.mockResolvedValue(page(3, 3))
    const { list, scope } = build()
    await flushPromises()
    api.listAgentThreads.mockClear()

    await list.nextPage()

    expect(api.listAgentThreads).not.toHaveBeenCalled()
    scope.stop()
  })

  it('删空当前页后自动退回上一页，而不是停在一个永久空白的页上', async () => {
    // 第 1 次：第 2 页有内容。第 2 次（删除后刷新）：第 2 页空了。第 3 次：自动退回第 1 页。
    api.listAgentThreads
      .mockResolvedValueOnce(page(THREAD_PAGE_SIZE, 21))
      .mockResolvedValueOnce({ items: [thread(21)], total: 21 })
      .mockResolvedValueOnce({ items: [], total: 20 })
      .mockResolvedValueOnce(page(THREAD_PAGE_SIZE, 20))
    api.deleteAgentThread.mockResolvedValue({ thread_id: thread(21).thread_id })
    const { list, scope } = build()
    await flushPromises()
    await list.nextPage()

    await list.remove(thread(21))

    expect(list.offset.value).toBe(0)
    expect(list.threads.value).toHaveLength(THREAD_PAGE_SIZE)
    scope.stop()
  })

  it('删除前必须确认；点取消就什么都不做', async () => {
    api.listAgentThreads.mockResolvedValue(page(1))
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))
    const { list, scope } = build()
    await flushPromises()

    await list.remove(thread(1))

    expect(api.deleteAgentThread).not.toHaveBeenCalled()
    scope.stop()
  })

  it('删掉的正是当前打开的那个时通知调用方', async () => {
    const target = thread(1)
    api.listAgentThreads.mockResolvedValue(page(1))
    api.deleteAgentThread.mockResolvedValue({ thread_id: target.thread_id })
    const { list, scope, onActiveThreadDeleted } = build({
      activeThreadId: () => target.thread_id,
    })
    await flushPromises()

    await list.remove(target)

    expect(onActiveThreadDeleted).toHaveBeenCalledOnce()
    scope.stop()
  })

  it('删的不是当前打开的那个就不通知，界面不该被清掉', async () => {
    api.listAgentThreads.mockResolvedValue(page(2))
    api.deleteAgentThread.mockResolvedValue({ thread_id: thread(2).thread_id })
    const { list, scope, onActiveThreadDeleted } = build({
      activeThreadId: () => thread(1).thread_id,
    })
    await flushPromises()

    await list.remove(thread(2))

    expect(onActiveThreadDeleted).not.toHaveBeenCalled()
    scope.stop()
  })

  it('删除中的那一行在结束后解除忙态，无论成败', async () => {
    api.listAgentThreads.mockResolvedValue(page(1))
    api.deleteAgentThread.mockRejectedValue(
      new ApiError({ message: '失败', code: 'agent_thread_database_unavailable', status: 503 }),
    )
    const { list, scope } = build()
    await flushPromises()

    await list.remove(thread(1))

    // 少了 finally 那一行，这一行会永久停在禁用态，用户只能刷新整页。
    expect(list.isDeleting(thread(1).thread_id)).toBe(false)
    expect(list.listError.value?.title).toBeTruthy()
    scope.stop()
  })

  it('删除时拿到 404 就刷新列表，让那一行消失', async () => {
    // 别处（另一个标签页）已经删掉了。留着它用户只会再点一次，拿到同一个错误。
    api.listAgentThreads.mockResolvedValue(page(1))
    api.deleteAgentThread.mockRejectedValue(
      new ApiError({ message: '不存在', code: 'agent_thread_not_found', status: 404 }),
    )
    const { list, scope } = build()
    await flushPromises()
    api.listAgentThreads.mockClear()
    api.listAgentThreads.mockResolvedValue({ items: [], total: 0 })

    await list.remove(thread(1))

    expect(api.listAgentThreads).toHaveBeenCalledOnce()
    scope.stop()
  })

  it('读取失败时给出可显示的文案，状态转成 error', async () => {
    api.listAgentThreads.mockRejectedValue(
      new ApiError({
        message: '库挂了',
        code: 'agent_thread_database_unavailable',
        status: 503,
        retryable: true,
      }),
    )
    const { list, scope } = build()

    await flushPromises()

    expect(list.listState.value).toBe('error')
    expect(list.listError.value?.title).toBe('会话记录暂时读不出来')
    expect(list.listError.value?.retryable).toBe(true)
    scope.stop()
  })

  it('新建的会话并进列表头部，不重复也不越过页大小', async () => {
    api.listAgentThreads.mockResolvedValue(page(THREAD_PAGE_SIZE, THREAD_PAGE_SIZE))
    const { list, scope } = build()
    await flushPromises()
    const created = thread(999, { title: '刚建的' })

    list.acceptCreatedThread(created)
    list.acceptCreatedThread(created)

    expect(list.threads.value).toHaveLength(THREAD_PAGE_SIZE)
    expect(list.threads.value[0]?.title).toBe('刚建的')
    expect(list.total.value).toBe(THREAD_PAGE_SIZE + 1)
    scope.stop()
  })

  it('不在第一页时不并入新会话', async () => {
    // 并进去会让它出现在一个它本不属于的页上，翻回第一页又会看到一遍。
    api.listAgentThreads.mockResolvedValue(page(THREAD_PAGE_SIZE, 60))
    const { list, scope } = build()
    await flushPromises()
    await list.nextPage()

    list.acceptCreatedThread(thread(999))

    expect(list.threads.value.some((item) => item.thread_id === thread(999).thread_id)).toBe(false)
    scope.stop()
  })

  it('总数为 0 且读取成功才算空态', async () => {
    api.listAgentThreads.mockResolvedValue({ items: [], total: 0 })
    const { list, scope } = build()

    expect(list.isEmpty.value).toBe(false) // 还在 loading，别先显示「还没有会话」
    await flushPromises()

    expect(list.isEmpty.value).toBe(true)
    scope.stop()
  })
})
