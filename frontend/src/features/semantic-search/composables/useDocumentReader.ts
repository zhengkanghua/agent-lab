import { computed, onScopeDispose, ref, shallowRef } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '@/api/client'
import { fetchDocument } from '@/api/documents'
import { toNewsDocumentDetail } from '../model/document-detail'
import type { NewsReadableResult } from '../model/search-result'

export function documentDetailQueryKey(documentId: string, contentHash: string) {
  // UUID 与 SHA-256 十六进制字符串大小写不影响业务身份；统一 key 可避免同一
  // 新闻因后端序列化大小写差异产生两份全文缓存。
  return ['document-detail', documentId.toLowerCase(), contentHash.toLowerCase()] as const
}

export function useDocumentReader() {
  const queryClient = useQueryClient()
  const isOpen = ref(false)
  const selectedResult = shallowRef<NewsReadableResult | null>(null)
  const triggerElement = shallowRef<HTMLButtonElement | null>(null)

  const queryKey = computed(() => {
    const selected = selectedResult.value
    return selected
      ? documentDetailQueryKey(selected.documentId, selected.contentHash)
      : documentDetailQueryKey('none', 'none')
  })

  const detailQuery = useQuery({
    queryKey,
    enabled: computed(() => isOpen.value && selectedResult.value !== null),
    queryFn: async ({ signal, queryKey: activeKey }) => {
      const documentId = String(activeKey[1])
      return fetchDocument({ documentId, signal })
    },
    retry: false,
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
  })

  const detail = computed(() => {
    const dto = detailQuery.data.value
    const selected = selectedResult.value
    // Query 失败时 TanStack Query 可能保留上一次成功缓存；错误面板不能同时展示
    // 那份旧正文或旧 hash，否则用户会误以为当前请求已经读取成功。
    if (
      !dto ||
      detailQuery.error.value ||
      !selected ||
      dto.document_id.toLowerCase() !== selected.documentId.toLowerCase()
    ) {
      return null
    }
    return toNewsDocumentDetail(dto)
  })

  const error = computed<ApiError | null>(() => {
    const value = detailQuery.error.value
    if (!value) return null
    return value instanceof ApiError
      ? value
      : new ApiError({
          message: 'Unexpected document loading failure.',
          code: 'unknown_error',
          cause: value,
        })
  })

  const isLoading = computed(
    () =>
      isOpen.value &&
      detail.value === null &&
      error.value === null &&
      (detailQuery.isPending.value || detailQuery.isFetching.value),
  )

  const contentHashMismatch = computed(
    () =>
      detail.value !== null &&
      selectedResult.value !== null &&
      detail.value.contentHash.toLowerCase() !== selectedResult.value.contentHash.toLowerCase(),
  )

  async function open(
    result: NewsReadableResult,
    trigger: HTMLButtonElement | null = null,
  ): Promise<void> {
    const previous = selectedResult.value
    selectedResult.value = result
    triggerElement.value = trigger
    isOpen.value = true

    if (
      previous &&
      (previous.documentId.toLowerCase() !== result.documentId.toLowerCase() ||
        previous.contentHash.toLowerCase() !== result.contentHash.toLowerCase())
    ) {
      await queryClient.cancelQueries({
        queryKey: documentDetailQueryKey(previous.documentId, previous.contentHash),
        exact: true,
      })
    }
  }

  async function close(): Promise<void> {
    const selected = selectedResult.value
    isOpen.value = false
    selectedResult.value = null
    if (selected) {
      await queryClient.cancelQueries({
        queryKey: documentDetailQueryKey(selected.documentId, selected.contentHash),
        exact: true,
      })
    }
  }

  function restoreFocus(): void {
    if (isOpen.value) return
    const trigger = triggerElement.value
    triggerElement.value = null
    trigger?.focus({ preventScroll: true })
  }

  async function retry(): Promise<void> {
    await detailQuery.refetch({ cancelRefetch: true })
  }

  onScopeDispose(() => {
    const selected = selectedResult.value
    if (selected) {
      void queryClient.cancelQueries({
        queryKey: documentDetailQueryKey(selected.documentId, selected.contentHash),
        exact: true,
      })
    }
  })

  return {
    isOpen,
    selectedResult,
    detail,
    error,
    isLoading,
    contentHashMismatch,
    open,
    close,
    restoreFocus,
    retry,
  }
}
