import { computed, reactive, ref, watch } from 'vue'
import { useQueries, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  createScheduledJob,
  deleteScheduledJob,
  listScheduledJobs,
  listScheduledTaskTypes,
  getScheduledJobRun,
  triggerScheduledJob,
  updateScheduledJob,
  type JobRunDto,
  type ScheduledJobDto,
} from '@/api/scheduled-jobs'
import { scheduledJobKeys } from '../constants/query-keys'
import { presentJobError } from '../model/job-error'
import { executionStatusLabel, formatRunStats } from '../model/job-copy'
import { ApiError } from '@/api/client'
import { supportsJobForm } from '../model/job-validation'
import type { JobSubmitPayload } from './useJobForm'

export type ScheduledJobLoadState = 'loading' | 'error' | 'ready'

export interface ExpandedPanel {
  jobId: string
  kind: 'edit' | 'history'
}

export interface UseScheduledJobDirectoryOptions {
  accountId: string
  /**
   * 被跟踪的手动触发进入终态时回调（成功或失败）。反馈归本 composable 写，
   * 这个口子留给页面做额外的跳转或提示。
   */
  onRunFinished?: (outcome: { job: ScheduledJobDto; run: JobRunDto }) => void
}

/**
 * 定时任务目录的状态与请求。
 *
 * 列表是 useQuery；行内操作（启停、触发、删除、创建）刻意用普通 async 函数 +
 * 手动失效缓存，而不是六个 useMutation 对象——这些操作的成功反馈都要落到具体行或
 * 全局 feedback 位，包装成 mutation 只会多一层没有收益的间接。查询侧（列表、执行
 * 历史）保持 Vue Query，享受缓存、去重和轮询。
 */
export function useScheduledJobDirectory(options: UseScheduledJobDirectoryOptions) {
  const queryClient = useQueryClient()

  const feedback = ref('')
  const busyJobIds = ref(new Set<string>())
  const rowErrors = ref<Record<string, string>>({})
  /** 当前展开的面板（编辑表单或执行历史）；整页同时只开一个，保持列表安静。 */
  const expanded = ref<ExpandedPanel | null>(null)
  /** 手动触发后等待终态的执行记录 id → 任务 id（Q3 共识：轮询到终态为止）。 */
  const receiptKey = `scheduled-job-receipts:${options.accountId}`
  const awaitedRunIds = reactive(new Map<string, string>())
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(receiptKey) ?? '[]')
    if (Array.isArray(saved)) {
      for (const pair of saved) {
        if (
          Array.isArray(pair) &&
          pair.length === 2 &&
          pair.every((value) => typeof value === 'string')
        ) {
          awaitedRunIds.set(pair[0], pair[1])
        }
      }
    }
  } catch {
    // 浏览器禁止存储或旧内容损坏时，仍可正常查询服务端记录。
  }
  watch(
    awaitedRunIds,
    () => {
      try {
        sessionStorage.setItem(receiptKey, JSON.stringify([...awaitedRunIds]))
      } catch {
        // 存储失败不影响已受理的任务执行。
      }
    },
    { flush: 'sync' },
  )

  const query = useQuery({
    queryKey: scheduledJobKeys.jobs(),
    queryFn: async ({ signal }) => listScheduledJobs(signal),
    staleTime: 10_000,
    refetchInterval: (state) =>
      state.state.data?.some((job) => job.active_run) || awaitedRunIds.size ? 5000 : false,
  })

  const typesQuery = useQuery({
    queryKey: [...scheduledJobKeys.all, 'types'],
    queryFn: ({ signal }) => listScheduledTaskTypes(signal),
    staleTime: 60_000,
  })
  const tracked = useQueries({
    queries: computed(() =>
      [...awaitedRunIds].map(([runId, jobId]) => ({
        queryKey: [...scheduledJobKeys.runs(jobId), runId],
        queryFn: ({ signal }: { signal: AbortSignal }) => getScheduledJobRun(jobId, runId, signal),
        refetchInterval: 5000,
        retry: false,
      })),
    ),
  })
  watch(tracked, (results) => {
    const queryFailure = '暂时无法查询任务执行结果，正在继续查询；不会重复提交。'
    const receipts = [...awaitedRunIds]
    for (const [index, result] of results.entries()) {
      const record = result.data
      if (record && !result.isError && rowErrors.value[record.job_id] === queryFailure) {
        clearRowError(record.job_id)
      }
      if (
        record &&
        (record.status !== 'running' || record.needs_attention) &&
        awaitedRunIds.has(record.id)
      ) {
        handleRunFinished(record.job_id, record)
      } else if (result.isError) {
        const receipt = receipts[index]
        if (!receipt) continue
        const [runId, jobId] = receipt
        if (result.error instanceof ApiError && result.error.status === 404) {
          awaitedRunIds.delete(runId)
          setRowError(jobId, '任务执行记录已不可查询，无法确认执行结果。')
          feedback.value = '任务执行记录已不可查询，无法确认执行结果。'
        } else {
          setRowError(jobId, queryFailure)
        }
      }
    }
  })

  const loadState = computed<ScheduledJobLoadState>(() => {
    if (query.isPending.value) return 'loading'
    if (query.isError.value) return 'error'
    return 'ready'
  })

  const loadError = computed(() => {
    if (query.isError.value) {
      return presentJobError(query.error.value, '读取定时任务失败，请刷新重试。')
    }
    return ''
  })

  const jobs = computed(() => query.data.value ?? [])

  function invalidateJobs(): void {
    void queryClient.invalidateQueries({ queryKey: scheduledJobKeys.jobs() })
  }

  function invalidateRuns(jobId: string): void {
    void queryClient.invalidateQueries({ queryKey: scheduledJobKeys.runs(jobId) })
  }

  function setBusy(jobId: string, busy: boolean): void {
    const next = new Set(busyJobIds.value)
    if (busy) next.add(jobId)
    else next.delete(jobId)
    busyJobIds.value = next
  }

  function setRowError(jobId: string, message: string): void {
    rowErrors.value = { ...rowErrors.value, [jobId]: message }
  }

  function clearRowError(jobId: string): void {
    if (rowErrors.value[jobId] === undefined) return
    const next = { ...rowErrors.value }
    delete next[jobId]
    rowErrors.value = next
  }

  function togglePanel(jobId: string, kind: ExpandedPanel['kind']): void {
    if (expanded.value?.jobId === jobId && expanded.value.kind === kind) {
      expanded.value = null
      return
    }
    const job = jobs.value.find((item) => item.id === jobId)
    if (
      kind === 'edit' &&
      (!job || job.enabled || job.active_run || !supportsJobForm(job.task_type))
    )
      return
    expanded.value = { jobId, kind }
  }

  function closePanel(): void {
    expanded.value = null
  }

  async function createJob(payload: JobSubmitPayload): Promise<void> {
    await createScheduledJob({
      key: payload.key ?? '',
      taskType: payload.taskType,
      cronExpr: payload.cronExpr,
      params: payload.params,
      enabled: payload.enabled,
    })
    invalidateJobs()
    feedback.value = `定时任务「${payload.key ?? ''}」已创建。`
  }

  async function updateJob(job: ScheduledJobDto, payload: JobSubmitPayload): Promise<void> {
    await updateScheduledJob({
      jobId: job.id,
      cronExpr: payload.cronExpr,
      params: payload.params,
      enabled: false,
    })
    invalidateJobs()
    invalidateRuns(job.id)
    feedback.value = `定时任务「${job.key}」已更新。`
    closePanel()
  }

  async function toggleEnabled(job: ScheduledJobDto, next: boolean): Promise<void> {
    if (busyJobIds.value.has(job.id)) return
    clearRowError(job.id)
    setBusy(job.id, true)
    try {
      await updateScheduledJob({ jobId: job.id, enabled: next })
      invalidateJobs()
      feedback.value = `定时任务「${job.key}」已${next ? '启用' : '停用'}。`
    } catch (error) {
      setRowError(job.id, presentJobError(error, '修改启停状态失败，请稍后重试。'))
    } finally {
      setBusy(job.id, false)
    }
  }

  async function runNow(job: ScheduledJobDto): Promise<void> {
    if (
      busyJobIds.value.has(job.id) ||
      job.active_run ||
      [...awaitedRunIds.values()].includes(job.id)
    )
      return
    clearRowError(job.id)
    setBusy(job.id, true)
    try {
      const receipt = await triggerScheduledJob(job.id)
      awaitedRunIds.set(receipt.run_id, job.id)
      invalidateJobs()
      invalidateRuns(job.id)
      // 直接纳客：展开这个任务的历史面板，轮询会把它带到终态。
      expanded.value = { jobId: job.id, kind: 'history' }
      feedback.value = `「${job.key}」已受理，正在后台执行。`
    } catch (error) {
      const rejected =
        error instanceof ApiError &&
        error.status !== undefined &&
        error.status >= 400 &&
        error.status < 500
      setRowError(
        job.id,
        rejected
          ? presentJobError(error, '触发被拒绝。')
          : '受理情况待核实，请查询执行记录；不会自动重复提交。',
      )
      expanded.value = { jobId: job.id, kind: 'history' }
      invalidateJobs()
      invalidateRuns(job.id)
    } finally {
      setBusy(job.id, false)
    }
  }

  async function removeJob(job: ScheduledJobDto): Promise<void> {
    if (busyJobIds.value.has(job.id) || job.active_run) return
    clearRowError(job.id)
    setBusy(job.id, true)
    try {
      await deleteScheduledJob(job.id)
      if (expanded.value?.jobId === job.id) closePanel()
      invalidateJobs()
      feedback.value = `定时任务「${job.key}」已删除。`
    } catch (error) {
      setRowError(job.id, presentJobError(error, '删除任务失败，请稍后重试。'))
    } finally {
      setBusy(job.id, false)
    }
  }

  /** useJobRuns 检测到被跟踪的执行进入终态时调用。 */
  function handleRunFinished(jobId: string, run: JobRunDto): void {
    if (!awaitedRunIds.has(run.id)) return
    awaitedRunIds.delete(run.id)
    invalidateJobs()
    invalidateRuns(jobId)
    feedback.value = `手动执行：${executionStatusLabel(run)}。${formatRunStats(run)}`
    const job = jobs.value.find((item) => item.id === jobId)
    if (job !== undefined) options.onRunFinished?.({ job, run })
  }

  function load(): void {
    void query.refetch()
    void typesQuery.refetch()
  }

  return {
    taskTypes: computed(() => typesQuery.data.value ?? []),
    taskTypesError: computed(() =>
      typesQuery.isError.value ? '读取任务类型失败，请刷新重试。' : '',
    ),
    jobs,
    loadState,
    loadError,
    feedback,
    busyJobIds,
    rowErrors,
    expanded,
    awaitedRunIds,
    editingJob: computed(() => {
      if (expanded.value?.kind !== 'edit') return null
      return jobs.value.find((job) => job.id === expanded.value?.jobId) ?? null
    }),
    togglePanel,
    closePanel,
    createJob,
    updateJob,
    toggleEnabled,
    runNow,
    removeJob,
    handleRunFinished,
    clearFeedback: (): void => {
      feedback.value = ''
    },
    load,
  }
}
