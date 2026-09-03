import { computed, ref } from 'vue'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  deleteAgentThread,
  listAgentThreads,
  type AgentThreadSummaryDto,
} from '@/api/agent-threads'
import { ApiError } from '@/api/client'
import { presentAgentError, type AgentErrorPresentation } from '../model/agent-error'
import { agentChatKeys } from '../constants/query-keys'

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
 * 会话列表的状态、分页与删除（已重构为 Vue Query 驱动）。
 */
export function useThreadList(options: UseThreadListOptions) {
  const queryClient = useQueryClient()
  const offset = ref(0)
  const listErrorOverride = ref<AgentErrorPresentation | null>(null)

  // Vue Query 帮你干掉了原先手写的 60 行「连点防抖、竞态拦截、异常重试」。
  const query = useQuery({
    queryKey: computed(() => [...agentChatKeys.threads(), offset.value]),
    queryFn: async ({ signal }) => {
      const page = await listAgentThreads({
        limit: THREAD_PAGE_SIZE,
        offset: offset.value,
        signal,
      })
      // 删到本页空了、但前面还有内容时自动退一页。
      if (page.items.length === 0 && offset.value > 0) {
        offset.value = Math.max(0, offset.value - THREAD_PAGE_SIZE)
      }
      return page
    },
    // 列表保持一定的存活时间，防止切页面回来立刻白屏刷新
    staleTime: 5000,
  })

  const threads = computed(() => query.data.value?.items ?? [])
  const total = computed(() => query.data.value?.total ?? 0)

  // 映射回原有的状态定义，兼容现有 UI 组件
  const listState = computed<ThreadListState>(() => {
    if (query.isPending.value) return 'loading'
    if (query.isError.value || listErrorOverride.value) return 'error'
    return 'ready'
  })

  const listError = computed(() => {
    if (listErrorOverride.value) return listErrorOverride.value
    if (query.error.value) return presentThreadError(query.error.value, '暂时无法读取会话列表。')
    return null
  })

  const hasMore = computed(() => offset.value + threads.value.length < total.value)
  const hasPrevious = computed(() => offset.value > 0)
  const isEmpty = computed(() => listState.value === 'ready' && total.value === 0)

  function load() {
    listErrorOverride.value = null
    return query.refetch()
  }

  function nextPage(): Promise<void> {
    if (!hasMore.value) return Promise.resolve()
    offset.value += THREAD_PAGE_SIZE
    return Promise.resolve()
  }

  function previousPage(): Promise<void> {
    if (!hasPrevious.value) return Promise.resolve()
    offset.value = Math.max(0, offset.value - THREAD_PAGE_SIZE)
    return Promise.resolve()
  }

  const deleteMutation = useMutation({
    mutationFn: (threadId: string) => deleteAgentThread(threadId),
    onSuccess: (_, deletedThreadId) => {
      // 删的正是当前打开的那个：先让页面清空界面，再刷新列表。
      if (options.activeThreadId() === deletedThreadId) {
        options.onActiveThreadDeleted()
      }
      // 告诉 Vue Query: threads 的数据脏了，请在后台自动刷新它。
      queryClient.invalidateQueries({ queryKey: agentChatKeys.threads() })
    },
    onError: (cause) => {
      listErrorOverride.value = presentThreadError(cause, '删除会话失败，请稍后重试。')
      if (cause instanceof ApiError && cause.status === 404) {
        queryClient.invalidateQueries({ queryKey: agentChatKeys.threads() })
      }
    },
  })

  async function remove(thread: AgentThreadSummaryDto): Promise<void> {
    if (deleteMutation.isPending.value && deleteMutation.variables.value === thread.thread_id)
      return
    if (!window.confirm(`删除会话「${thread.title}」？对话历史会一起清除，且无法恢复。`)) return

    listErrorOverride.value = null
    try {
      await deleteMutation.mutateAsync(thread.thread_id)
    } catch {
      // 错误已经在 onError 中处理，这里只需捕获以防止外部抛出未处理的 promise rejection
    }
  }

  /** 新开的会话刚产生 id 时把它并进列表头部，省掉一次整页请求。 */
  function acceptCreatedThread(thread: AgentThreadSummaryDto): void {
    if (offset.value !== 0) return

    // Optimistic Update: 直接修改 Vue Query 的缓存
    queryClient.setQueryData(
      [...agentChatKeys.threads(), offset.value],
      (oldData: { items: AgentThreadSummaryDto[]; total: number } | undefined) => {
        if (!oldData) return { items: [thread], total: 1 }
        if (oldData.items.some((existing) => existing.thread_id === thread.thread_id))
          return oldData
        return {
          ...oldData,
          items: [thread, ...oldData.items].slice(0, THREAD_PAGE_SIZE),
          total: oldData.total + 1,
        }
      },
    )
  }

  function isDeleting(threadId: string): boolean {
    return deleteMutation.isPending.value && deleteMutation.variables.value === threadId
  }

  // 暴露一个 Set 保持和原来 UI 层的兼容
  const deletingThreadIds = computed(() => {
    const set = new Set<string>()
    if (deleteMutation.isPending.value && deleteMutation.variables.value) {
      set.add(deleteMutation.variables.value as string)
    }
    return set
  })

  function presentThreadError(cause: unknown, fallbackMessage: string): AgentErrorPresentation {
    return presentAgentError(
      cause instanceof ApiError
        ? cause
        : new ApiError({ message: fallbackMessage, code: 'unknown_error', cause }),
    )
  }

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
