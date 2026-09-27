import { computed, ref, watch, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import {
  USAGE_PAGE_SIZE,
  fetchUsageModels,
  fetchUsageRecords,
  fetchUsageSummary,
  type UsageFilters,
  type UsageRecord,
  type UsageSummary,
} from '@/api/usage'
import { ApiError } from '@/api/client'
import { usageKeys } from '../constants/query-keys'

export type UsageState = 'loading' | 'ready' | 'error'

export interface UseUsageResult {
  summary: Ref<UsageSummary | undefined>
  records: Ref<UsageRecord[]>
  models: Ref<string[]>
  /** 汇总与明细共用：两处一起取、一起失败，界面不该出现「数字有了、列表还在转」的半截状态。 */
  state: Ref<UsageState>
  errorMessage: Ref<string>
  hasMore: Ref<boolean>
  hasPrevious: Ref<boolean>
  isEmpty: Ref<boolean>
  nextPage: () => void
  previousPage: () => void
  refresh: () => Promise<unknown>
}

/**
 * 设置中心 · 用量分区的数据层。
 *
 * 汇总与明细用**同一组筛选**作为查询键的一部分，所以筛选一变两处一起重新取数——「改动任一个，
 * 两处一起变」是键设计的结果，不靠调用方记得同时刷新两处。
 *
 * 模型名列表不跟筛选走（后端也只按账号过滤）：跟着走的话，选了某个模型再改时间范围，选项里
 * 那个模型可能消失，而下拉还选着它。
 */
export function useUsage(filters: Ref<UsageFilters>): UseUsageResult {
  const offset = ref(0)

  // 筛选一变就退回第一页，并让明细与汇总一起重新取。留在第 3 页会让用户看到「筛选结果为空」
  // 而其实前两页有内容。
  watch(
    () => [filters.value.model, filters.value.start, filters.value.end],
    () => {
      offset.value = 0
    },
  )

  const summaryQuery = useQuery({
    queryKey: computed(() => usageKeys.summary(filters.value)),
    queryFn: ({ signal }) => fetchUsageSummary(filters.value, signal),
    staleTime: 5_000,
  })

  const recordsQuery = useQuery({
    queryKey: computed(() => usageKeys.records(filters.value, offset.value)),
    queryFn: ({ signal }) =>
      fetchUsageRecords(filters.value, { limit: USAGE_PAGE_SIZE, offset: offset.value }, signal),
    staleTime: 5_000,
  })

  const modelsQuery = useQuery({
    queryKey: usageKeys.models(),
    queryFn: ({ signal }) => fetchUsageModels(signal),
    // 模型名基本不变，缓存久一点；它只是筛选栏的选项，不值得每次进入分区都重取。
    staleTime: 60_000,
  })

  const state = computed<UsageState>(() => {
    if (summaryQuery.isPending.value || recordsQuery.isPending.value) return 'loading'
    if (summaryQuery.isError.value || recordsQuery.isError.value) return 'error'
    return 'ready'
  })

  const errorMessage = computed(() => {
    const error = summaryQuery.error.value ?? recordsQuery.error.value
    if (error === null) return ''
    if (error instanceof ApiError && error.status === 503) {
      return '用量库当前不可用，暂时读不到数据。请稍后重试。'
    }
    return '读取用量数据失败，请稍后重试。'
  })

  const records = computed(() => recordsQuery.data.value?.items ?? [])
  const hasMore = computed(() => recordsQuery.data.value?.hasMore ?? false)
  const hasPrevious = computed(() => offset.value > 0)
  const isEmpty = computed(() => state.value === 'ready' && records.value.length === 0)

  function nextPage(): void {
    if (hasMore.value) offset.value += USAGE_PAGE_SIZE
  }

  function previousPage(): void {
    offset.value = Math.max(0, offset.value - USAGE_PAGE_SIZE)
  }

  function refresh(): Promise<unknown> {
    return Promise.all([summaryQuery.refetch(), recordsQuery.refetch()])
  }

  return {
    summary: computed(() => summaryQuery.data.value),
    records,
    models: computed(() => modelsQuery.data.value ?? []),
    state,
    errorMessage,
    hasMore,
    hasPrevious,
    isEmpty,
    nextPage,
    previousPage,
    refresh,
  }
}
