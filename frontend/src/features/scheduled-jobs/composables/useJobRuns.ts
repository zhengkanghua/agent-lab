import { computed, watch, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { listScheduledJobRuns, type JobRunDto } from '@/api/scheduled-jobs'
import { scheduledJobKeys } from '../constants/query-keys'

/** 历史面板一次拉取的条数；后端上限 100，这里取 20 条（Q2 共识）。 */
export const JOB_RUNS_LIMIT = 20
/** 历史面板打开且有执行在途时的轮询间隔（Q3 共识）。 */
export const JOB_RUNS_POLL_INTERVAL_MS = 5000

export interface UseJobRunsOptions {
  jobId: Ref<string>
  enabled: Ref<boolean>
  /** 该任务是否有正在等待终态的手动触发（有才轮询）。 */
  hasAwaitedRun: Ref<boolean>
  /** 这个执行 id 是否是被跟踪的手动触发（由目录持有的 awaited 表判定）。 */
  isAwaited: (runId: string) => boolean
  /** 被跟踪的执行进入终态时回调；同一 id 只回调一次（目录收到即从表里删掉）。 */
  onAwaitedFinished: (run: JobRunDto) => void
}

/**
 * 单个任务的执行历史查询。面板打开才拉；面板开着且「最新一条仍在执行」或「有被
 * 跟踪的手动触发」时每 5 秒轮询，全部落定后自动停——不会常驻空转。
 */
export function useJobRuns(options: UseJobRunsOptions) {
  // 轮询判定要读 query 自身的 data，直接写在 useQuery 的 refetchInterval 里会构成
  // 「query 依赖自己」的循环推断；先用可替换的判定函数占位，查询建好后再接上。
  let pollDecider = (): boolean => false

  const query = useQuery({
    queryKey: computed(() => scheduledJobKeys.runs(options.jobId.value)),
    queryFn: async ({ signal }) =>
      listScheduledJobRuns(options.jobId.value, JOB_RUNS_LIMIT, signal),
    enabled: options.enabled,
    staleTime: 5_000,
    refetchInterval: (): number | false =>
      options.enabled.value && pollDecider() ? JOB_RUNS_POLL_INTERVAL_MS : false,
  })

  pollDecider = (): boolean =>
    options.hasAwaitedRun.value ||
    (query.data.value?.some((run) => run.status === 'running') ?? false)

  watch(query.data, (runs) => {
    if (!runs) return
    for (const run of runs) {
      if (run.status !== 'running' && options.isAwaited(run.id)) {
        options.onAwaitedFinished(run)
      }
    }
  })

  return query
}
