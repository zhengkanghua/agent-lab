import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { ApiError } from '@/api/client'
import {
  cancelTask,
  getTaskRun,
  isTerminalStatus,
  listTaskRuns,
  RUN_STATUSES,
  type RunStatus,
} from '@/api/tasks'
import { isUuid } from '@/api/json-guards'
import { presentJobError } from '../model/job-error'
import { selectedRunStorageKey } from './useTaskSubmissions'
import { scheduledJobKeys } from '../constants/query-keys'

/** 列表可翻页，详情仅按编号查询；路由与账号级回执让离页、删除配置都不丢查询入口。 */
export function useTaskExecutions(accountId: string) {
  const route = useRoute()
  const router = useRouter()
  const client = useQueryClient()
  const selectedId = ref('')
  const lookupId = ref('')
  const lookupError = ref('')
  const view = ref<'configurations' | 'executions'>('configurations')
  const status = ref<RunStatus | ''>('')
  const taskType = ref('')
  const offset = ref(0)
  const pageSize = 20
  const operationError = ref('')
  const cancelling = ref(false)

  try {
    const saved = sessionStorage.getItem(selectedRunStorageKey(accountId))
    if (isUuid(saved)) selectedId.value = saved
  } catch {
    /* URL 中的执行编号仍可查询。 */
  }
  watch(
    () => [route.query.view, route.query.run],
    () => {
      const runId = route.query.run
      if (isUuid(runId)) selectedId.value = runId
      lookupId.value = selectedId.value
      view.value =
        route.query.view === 'configurations'
          ? 'configurations'
          : route.query.view === 'executions' || selectedId.value
            ? 'executions'
            : 'configurations'
    },
    { immediate: true },
  )

  function setView(next: 'configurations' | 'executions'): void {
    view.value = next
    void router.replace({
      query: { ...route.query, view: next, run: selectedId.value || undefined },
    })
  }

  function selectRun(runId: string): void {
    lookupId.value = runId.trim()
    if (!isUuid(lookupId.value)) {
      lookupError.value = '请输入完整的任务执行编号（UUID）。'
      return
    }
    lookupError.value = ''
    operationError.value = ''
    selectedId.value = lookupId.value
    try {
      sessionStorage.setItem(selectedRunStorageKey(accountId), selectedId.value)
    } catch {
      /* URL 保留编号。 */
    }
    setView('executions')
  }

  watch([status, taskType], () => {
    offset.value = 0
  })
  const list = useQuery({
    queryKey: computed(() => [
      'task-runs',
      accountId,
      'list',
      status.value,
      taskType.value,
      offset.value,
    ]),
    queryFn: ({ signal }) =>
      listTaskRuns(
        {
          offset: offset.value,
          limit: pageSize,
          status: status.value || undefined,
          taskType: taskType.value || undefined,
        },
        signal,
      ),
    enabled: computed(() => view.value === 'executions'),
    refetchInterval: 5000,
  })
  const detail = useQuery({
    queryKey: computed(() => ['task-runs', accountId, 'detail', selectedId.value]),
    queryFn: ({ signal }) => getTaskRun(selectedId.value, signal),
    enabled: computed(() => view.value === 'executions' && !!selectedId.value),
    refetchInterval: (query) => {
      if (query.state.error instanceof ApiError && [404, 410].includes(query.state.error.status))
        return false
      return query.state.data && isTerminalStatus(query.state.data.status) ? false : 5000
    },
    retry: false,
    staleTime: 0,
  })

  async function cancel(): Promise<void> {
    const runId = selectedId.value
    if (cancelling.value || !detail.data.value?.can_cancel) return
    cancelling.value = true
    operationError.value = ''
    try {
      const run = await cancelTask(runId)
      client.setQueryData(['task-runs', accountId, 'detail', runId], run)
      void client.invalidateQueries({ queryKey: ['task-runs', accountId, 'list'] })
      void client.invalidateQueries({ queryKey: scheduledJobKeys.jobs() })
    } catch (error) {
      if (selectedId.value === runId)
        operationError.value = presentJobError(error, '取消结果尚未确认，正在重新查询执行状态。')
      void detail.refetch()
    } finally {
      cancelling.value = false
    }
  }

  function refresh(): void {
    void list.refetch()
    if (selectedId.value) void detail.refetch()
  }

  function filterStatus(value: string): void {
    status.value = RUN_STATUSES.find((item) => item === value) ?? ''
  }

  return {
    view,
    setView,
    selectedId,
    lookupId,
    setLookupId: (value: string | number) => {
      lookupId.value = String(value)
    },
    lookupError,
    selectRun,
    status,
    filterStatus,
    taskType,
    filterTaskType: (value: string) => {
      taskType.value = value
    },
    offset,
    pageSize,
    changePage: (direction: -1 | 1) => {
      offset.value = Math.max(0, offset.value + direction * pageSize)
    },
    list,
    detail,
    operationError,
    cancelling,
    cancel,
    refresh,
    detailError: computed(() =>
      detail.isError.value
        ? presentJobError(detail.error.value, '读取执行详情失败，请稍后刷新。')
        : '',
    ),
    listError: computed(() =>
      list.isError.value
        ? presentJobError(list.error.value, '读取任务执行列表失败，请稍后刷新。')
        : '',
    ),
  }
}

export type TaskExecutions = ReturnType<typeof useTaskExecutions>
