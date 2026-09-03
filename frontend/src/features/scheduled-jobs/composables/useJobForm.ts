import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import type { ScheduledJobDto, ScheduledJobTaskType } from '@/api/scheduled-jobs'
import { presentJobError } from '../model/job-error'
import {
  BATCH_SIZE_DEFAULT,
  buildParams,
  LIMIT_PER_SOURCE_DEFAULT,
  STALE_AFTER_MINUTES_DEFAULT,
  validateParams,
  type JobFormErrors,
} from '../model/job-validation'

export interface JobSubmitPayload {
  /** 仅 create 模式携带。 */
  key?: string
  taskType: ScheduledJobTaskType
  cronExpr: string
  params: Record<string, number>
  enabled: boolean
}

export interface UseJobFormOptions {
  mode: 'create' | 'edit'
  /**
   * edit 模式的初始任务；传响应式引用（页面的 editingJob computed）时，展开哪一行
   * 字段就重置成哪一行。create 模式恒为 null。
   */
  job: MaybeRefOrGetter<ScheduledJobDto | null>
  /** 提交由调用方执行（创建走 create、编辑走 update）；抛错时错误落回本表单。 */
  onSubmit: (payload: JobSubmitPayload) => Promise<void>
  onClose: () => void
}

/**
 * 新建/编辑共用的表单状态。字段是受控值而不是组件内部 ref：页面要能在关闭时清空，
 * 组件碰不到页面持有的值（照 useAccountCreateForm 的分工）。
 *
 * cron 的语义校验不在这里——表单组件里的 useCronPreview 负责预览与提交闸门，
 * 本 composable 只做零成本的形状与范围校验。
 */
export function useJobForm(options: UseJobFormOptions) {
  const isCreate = options.mode === 'create'

  const key = ref('')
  const taskType = ref<ScheduledJobTaskType>('freshrss_sync')
  const cronExpr = ref('*/10 * * * *')
  const limitPerSource = ref(LIMIT_PER_SOURCE_DEFAULT)
  const batchSize = ref(BATCH_SIZE_DEFAULT)
  const staleAfterMinutes = ref(STALE_AFTER_MINUTES_DEFAULT)
  const enabled = ref(true)

  const errors = ref<JobFormErrors>({})
  const formError = ref('')
  const submitting = ref(false)

  function applyJob(job: ScheduledJobDto): void {
    key.value = job.key
    taskType.value = job.task_type as ScheduledJobTaskType
    cronExpr.value = job.cron_expr
    enabled.value = job.enabled
    limitPerSource.value = LIMIT_PER_SOURCE_DEFAULT
    batchSize.value = BATCH_SIZE_DEFAULT
    staleAfterMinutes.value = STALE_AFTER_MINUTES_DEFAULT
    if (job.task_type === 'freshrss_sync' && typeof job.params.limit_per_source === 'number') {
      limitPerSource.value = job.params.limit_per_source
    }
    if (job.task_type === 'index_pending') {
      if (typeof job.params.batch_size === 'number') batchSize.value = job.params.batch_size
      if (typeof job.params.stale_after_minutes === 'number') {
        staleAfterMinutes.value = job.params.stale_after_minutes
      }
    }
  }

  function reset(): void {
    key.value = ''
    taskType.value = 'freshrss_sync'
    cronExpr.value = '*/10 * * * *'
    limitPerSource.value = LIMIT_PER_SOURCE_DEFAULT
    batchSize.value = BATCH_SIZE_DEFAULT
    staleAfterMinutes.value = STALE_AFTER_MINUTES_DEFAULT
    enabled.value = true
    errors.value = {}
    formError.value = ''
  }

  if (isCreate) {
    reset()
  } else {
    // 编辑模式跟随 job 源变化：整页同时只有一个编辑面板，展开哪行就重置成哪行。
    watch(
      () => toValue(options.job),
      (job) => {
        errors.value = {}
        formError.value = ''
        if (job !== null) applyJob(job)
      },
      { immediate: true },
    )
  }

  function setTaskType(type: ScheduledJobTaskType): void {
    taskType.value = type
    // 换类型时参数回默认值：两类型的参数互不通用，保留旧值只会提交出非法形状。
    limitPerSource.value = LIMIT_PER_SOURCE_DEFAULT
    batchSize.value = BATCH_SIZE_DEFAULT
    staleAfterMinutes.value = STALE_AFTER_MINUTES_DEFAULT
    const job = toValue(options.job)
    if (job !== null && job.task_type === type) {
      if (type === 'freshrss_sync' && typeof job.params.limit_per_source === 'number') {
        limitPerSource.value = job.params.limit_per_source
      }
      if (type === 'index_pending') {
        if (typeof job.params.batch_size === 'number') batchSize.value = job.params.batch_size
        if (typeof job.params.stale_after_minutes === 'number') {
          staleAfterMinutes.value = job.params.stale_after_minutes
        }
      }
    }
  }

  /**
   * 提交表单。cron 的语义校验在表单组件的 cron 预览里（未通过时组件不会发出 submit），
   * 这里只做零成本的形状与范围校验；服务端仍可能 422，错误落回 formError。
   */
  async function submit(): Promise<void> {
    const values = {
      key: key.value,
      taskType: taskType.value,
      cronExpr: cronExpr.value,
      limitPerSource: limitPerSource.value,
      batchSize: batchSize.value,
      staleAfterMinutes: staleAfterMinutes.value,
      enabled: enabled.value,
    }
    errors.value = validateParams(values)
    if (!isCreate) delete errors.value.key
    if (Object.keys(errors.value).length > 0) return

    submitting.value = true
    formError.value = ''
    try {
      await options.onSubmit({
        ...(isCreate ? { key: key.value } : {}),
        taskType: taskType.value,
        cronExpr: cronExpr.value.trim(),
        params: buildParams(values),
        enabled: enabled.value,
      })
    } catch (error) {
      formError.value = presentJobError(error, '保存定时任务失败，请稍后重试。')
    } finally {
      submitting.value = false
    }
  }

  function close(): void {
    if (!submitting.value) options.onClose()
  }

  return {
    isCreate: computed(() => isCreate),
    editingJob: computed(() => toValue(options.job)),
    key,
    taskType,
    cronExpr,
    limitPerSource,
    batchSize,
    staleAfterMinutes,
    enabled,
    errors,
    formError,
    submitting,
    setTaskType,
    submit,
    close,
    reset,
  }
}

export type UseJobFormReturn = ReturnType<typeof useJobForm>
