import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import type {
  ScheduledJobDto,
  ScheduledJobTaskType,
  ScheduledTaskTypeDto,
} from '@/api/scheduled-jobs'
import { presentJobError } from '../model/job-error'
import { buildParams, validateParams, type JobFormErrors } from '../model/job-validation'

export interface JobSubmitPayload {
  key?: string
  taskType: ScheduledJobTaskType
  cronExpr: string
  params: Record<string, number | boolean | string[]>
  enabled: boolean
}

export interface UseJobFormOptions {
  mode: 'create' | 'edit'
  job: MaybeRefOrGetter<ScheduledJobDto | null>
  taskTypes: MaybeRefOrGetter<ScheduledTaskTypeDto[]>
  onSubmit: (payload: JobSubmitPayload) => Promise<void>
  onClose: () => void
}

export function useJobForm(options: UseJobFormOptions) {
  const isCreate = options.mode === 'create'
  const taskTypes = computed(() => toValue(options.taskTypes))
  const key = ref('')
  const taskType = ref('freshrss_sync')
  const cronExpr = ref('*/10 * * * *')
  const limitPerSource = ref(0)
  const batchSize = ref(0)
  const staleAfterMinutes = ref(0)
  const retentionDays = ref(0)
  const dryRun = ref(true)
  const knowledgeBaseIds = ref<string[]>([])
  const enabled = ref(true)
  const errors = ref<JobFormErrors>({})
  const formError = ref('')
  const submitting = ref(false)
  let defaultsApplied = false

  function applyParams(params: Record<string, unknown>): void {
    limitPerSource.value = Number(params.limit_per_source ?? 0)
    batchSize.value = Number(params.batch_size ?? 0)
    staleAfterMinutes.value = Number(params.stale_after_minutes ?? 0)
    retentionDays.value = Number(params.retention_days ?? 0)
    dryRun.value = params.dry_run !== false
    // 清理范围回填：历史配置可能没有该字段，落空数组由校验要求补选。
    knowledgeBaseIds.value = Array.isArray(params.knowledge_base_ids)
      ? params.knowledge_base_ids.map(String)
      : []
  }

  function setTaskType(type: string): void {
    taskType.value = type
    const defaults = taskTypes.value.find((item) => item.task_type === type)?.defaults
    defaultsApplied = defaults !== undefined
    applyParams(defaults ?? {})
  }

  function reset(): void {
    key.value = ''
    setTaskType('freshrss_sync')
    cronExpr.value = '*/10 * * * *'
    enabled.value = true
    errors.value = {}
    formError.value = ''
  }

  watch(
    taskTypes,
    () => {
      if (isCreate && !defaultsApplied && !submitting.value) setTaskType(taskType.value)
    },
    { immediate: true },
  )

  if (!isCreate) {
    // 轮询返回相同任务时保留草稿，切换编辑对象才重新填入配置。
    watch(
      () => toValue(options.job)?.id,
      () => {
        const job = toValue(options.job)
        if (!job) return
        key.value = job.key
        taskType.value = job.task_type
        cronExpr.value = job.cron_expr
        enabled.value = job.enabled
        applyParams(job.params)
        errors.value = {}
        formError.value = ''
      },
      { immediate: true },
    )
  }

  async function submit(): Promise<void> {
    if (submitting.value) return
    const values = {
      key: key.value,
      taskType: taskType.value,
      cronExpr: cronExpr.value,
      limitPerSource: limitPerSource.value,
      batchSize: batchSize.value,
      staleAfterMinutes: staleAfterMinutes.value,
      retentionDays: retentionDays.value,
      dryRun: dryRun.value,
      knowledgeBaseIds: knowledgeBaseIds.value,
      enabled: enabled.value,
    }
    errors.value = validateParams(
      values,
      taskTypes.value.find((item) => item.task_type === taskType.value),
    )
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
    taskTypes,
    key,
    taskType,
    cronExpr,
    limitPerSource,
    batchSize,
    staleAfterMinutes,
    retentionDays,
    dryRun,
    knowledgeBaseIds,
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
