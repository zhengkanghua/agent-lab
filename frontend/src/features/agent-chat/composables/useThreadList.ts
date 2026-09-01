import { computed, onScopeDispose, ref } from 'vue'
import {
  deleteAgentThread,
  listAgentThreads,
  type AgentThreadSummaryDto,
} from '@/api/agent-threads'
import { ApiError } from '@/api/client'
import { presentAgentError, type AgentErrorPresentation } from '../model/agent-error'

/** 一页取多少个会话。与后端 `DEFAULT_THREAD_PAGE_SIZE` 一致，改一边就要改另一边。 */
export const THREAD_PAGE_SIZE = 20

export type ThreadListState = 'loading' | 'ready' | 'error'

export interface UseThreadListOptions {
  /**
   * 删掉的正是当前打开的那个会话时执行。跳转归页面：它涉及 router，而 feature 不 import
   * 路由与页面（与 `useUserDirectory.onSelfDowngraded` 同一个理由）。
   */
  onActiveThreadDeleted: () => void
  /** 当前打开的会话 id，取 getter 而不是 Ref，调用点直接传 `() => chat.threadId.value`。 */
  activeThreadId: () => string | null
}

/**
 * 会话列表的状态、分页与删除。
 *
 * 列表本身是导航用的，所以这里刻意不缓存历史内容——点进某个会话时由 `useAgentChat.loadThread`
 * 单独去取。把历史也塞进列表项会让它变成 checkpointer 内容的副本，而历史压缩是破坏性的，
 * 副本迟早和模型真正看到的上下文分叉。
 */
export function useThreadList(options: UseThreadListOptions) {
  const threads = ref<AgentThreadSummaryDto[]>([])
  const total = ref(0)
  const offset = ref(0)
  const listState = ref<ThreadListState>('loading')
  const listError = ref<AgentErrorPresentation | null>(null)
  const deletingThreadIds = ref(new Set<string>())

  let loadController: AbortController | null = null

  const hasMore = computed(() => offset.value + threads.value.length < total.value)
  const hasPrevious = computed(() => offset.value > 0)
  const isEmpty = computed(() => listState.value === 'ready' && total.value === 0)

  /**
   * 读取当前那一页。
   *
   * 连点翻页会发多条请求，回来的顺序不保证与发出顺序一致。AbortController 拦不住
   * 「响应已到、await 还没恢复执行」那个窗口，所以另外比 controller 身份。
   */
  async function load(): Promise<void> {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    listState.value = 'loading'
    listError.value = null

    try {
      const page = await listAgentThreads({
        limit: THREAD_PAGE_SIZE,
        offset: offset.value,
        signal: controller.signal,
      })
      if (controller !== loadController) return
      threads.value = [...page.items]
      total.value = page.total
      listState.value = 'ready'

      // 删到本页空了、但前面还有内容时自动退一页。不退的话用户停在一个永远空白的页上，
      // 而「上一页」按钮是唯一出路——没人看得出该点它。
      if (page.items.length === 0 && offset.value > 0) {
        offset.value = Math.max(0, offset.value - THREAD_PAGE_SIZE)
        await load()
      }
    } catch (cause) {
      if (controller.signal.aborted || controller !== loadController) return
      listError.value = presentThreadError(cause, '暂时无法读取会话列表。')
      listState.value = 'error'
    }
  }

  function nextPage(): Promise<void> {
    if (!hasMore.value) return Promise.resolve()
    offset.value += THREAD_PAGE_SIZE
    return load()
  }

  function previousPage(): Promise<void> {
    if (!hasPrevious.value) return Promise.resolve()
    offset.value = Math.max(0, offset.value - THREAD_PAGE_SIZE)
    return load()
  }

  /**
   * 删除一个会话，删完刷新当前页。
   *
   * 二次确认留在这里而不是交给组件：它是这个动作的一部分——删除会连历史一起清掉且不可恢复。
   * 放到组件里就变成「某个按钮恰好问了一句」，换个入口调用同一个方法时会静默少掉这道确认。
   */
  async function remove(thread: AgentThreadSummaryDto): Promise<void> {
    if (deletingThreadIds.value.has(thread.thread_id)) return
    if (!window.confirm(`删除会话「${thread.title}」？对话历史会一起清除，且无法恢复。`)) return

    setDeleting(thread.thread_id, true)
    listError.value = null
    try {
      await deleteAgentThread(thread.thread_id)
      // 删的正是当前打开的那个：先让页面清空界面，再刷新列表。顺序反过来会有一瞬间
      // 「列表里已经没有它、右边还显示着它的历史」。
      if (options.activeThreadId() === thread.thread_id) {
        options.onActiveThreadDeleted()
      }
      await load()
    } catch (cause) {
      listError.value = presentThreadError(cause, '删除会话失败，请稍后重试。')
      // 已经被别处删掉时后端回 404。刷新一次让它从列表里消失——留着它用户只会再点一次，
      // 拿到同一个错误。
      if (cause instanceof ApiError && cause.status === 404) {
        await load()
      }
    } finally {
      setDeleting(thread.thread_id, false)
    }
  }

  /** 新开的会话刚产生 id 时把它并进列表头部，省掉一次整页请求。 */
  function acceptCreatedThread(thread: AgentThreadSummaryDto): void {
    if (threads.value.some((existing) => existing.thread_id === thread.thread_id)) return
    if (offset.value !== 0) return
    threads.value = [thread, ...threads.value].slice(0, THREAD_PAGE_SIZE)
    total.value += 1
  }

  function isDeleting(threadId: string): boolean {
    return deletingThreadIds.value.has(threadId)
  }

  /** 整只替换 Set：原地 add/delete 不会触发依赖这个 ref 的渲染。 */
  function setDeleting(threadId: string, deleting: boolean): void {
    const next = new Set(deletingThreadIds.value)
    if (deleting) next.add(threadId)
    else next.delete(threadId)
    deletingThreadIds.value = next
  }

  function presentThreadError(cause: unknown, fallbackMessage: string): AgentErrorPresentation {
    return presentAgentError(
      cause instanceof ApiError
        ? cause
        : new ApiError({ message: fallbackMessage, code: 'unknown_error', cause }),
    )
  }

  onScopeDispose(() => {
    loadController?.abort()
  })

  return {
    threads,
    total,
    offset,
    listState,
    listError,
    deletingThreadIds,
    hasMore,
    hasPrevious,
    isEmpty,
    load,
    nextPage,
    previousPage,
    remove,
    acceptCreatedThread,
    isDeleting,
  }
}
