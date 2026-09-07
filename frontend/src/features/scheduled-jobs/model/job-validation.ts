import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'
import { isRecord, isUuid } from '@/api/json-guards'

export interface JobFormValues {
  key: string
  taskType: string
  cronExpr: string
  limitPerSource: number
  batchSize: number
  staleAfterMinutes: number
  retentionDays: number
  dryRun: boolean
  knowledgeBaseIds: string[]
  enabled: boolean
}

export type JobFormErrors = Partial<Record<keyof JobFormValues | 'cron', string>>

/** 只声明已有表单的字段适配，类型清单与数值约束来自后端。 */
type ParameterField =
  | 'limitPerSource'
  | 'batchSize'
  | 'staleAfterMinutes'
  | 'retentionDays'
  | 'dryRun'
  | 'knowledgeBaseIds'
export const FORM_FIELDS: Record<string, Record<string, ParameterField>> = {
  freshrss_sync: { limit_per_source: 'limitPerSource' },
  index_pending: { batch_size: 'batchSize', stale_after_minutes: 'staleAfterMinutes' },
  prune_old_documents: {
    retention_days: 'retentionDays',
    dry_run: 'dryRun',
    knowledge_base_ids: 'knowledgeBaseIds',
  },
}

export function supportsJobForm(taskType: string): boolean {
  return Object.hasOwn(FORM_FIELDS, taskType)
}

export function parameterSchema(spec: ScheduledTaskTypeDto | undefined, field: string) {
  const properties = spec?.params_schema.properties
  const schema = isRecord(properties) ? properties[field] : undefined
  return isRecord(schema) ? schema : {}
}

export function parameterBounds(spec: ScheduledTaskTypeDto | undefined, field: string) {
  const schema = parameterSchema(spec, field)
  return {
    min: typeof schema.minimum === 'number' ? schema.minimum : undefined,
    max: typeof schema.maximum === 'number' ? schema.maximum : undefined,
  }
}

export function validateKey(key: string): string {
  if (key.trim().length === 0) return '请填写任务标识。'
  if (key.length < 3 || key.length > 64) return '任务标识需要 3–64 个字符。'
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(key))
    return '任务标识只能用小写字母、数字和短横线（不能以短横线开头或结尾）。'
  return ''
}

export function validateCronShape(cronExpr: string): string {
  const fields = cronExpr.trim().split(/\s+/)
  return fields.length !== 5 || fields.some((field) => field.length === 0)
    ? 'cron 表达式需要 5 段（分 时 日 月 周），用空格分隔。'
    : ''
}

export function validateParams(values: JobFormValues, spec?: ScheduledTaskTypeDto): JobFormErrors {
  const errors: JobFormErrors = {}
  if (!supportsJobForm(values.taskType) || spec?.task_type !== values.taskType) {
    errors.taskType = '任务类型信息未就绪或暂不支持编辑。'
  }
  const keyError = validateKey(values.key)
  if (keyError) errors.key = keyError
  const cronError = validateCronShape(values.cronExpr)
  if (cronError) errors.cron = cronError
  for (const [field, key] of Object.entries(FORM_FIELDS[values.taskType] ?? {})) {
    const value = values[key]
    const schema = parameterSchema(spec, field)
    const { min, max } = parameterBounds(spec, field)
    if (schema.type === 'boolean') {
      if (typeof value !== 'boolean') errors[key] = '请选择预演状态。'
    } else if (schema.type === 'array') {
      // 清理范围多选：后端拒绝空列表，这里同样要求至少选一个库。
      if (
        !Array.isArray(value) ||
        value.length === 0 ||
        !value.every((item) => typeof item === 'string' && isUuid(item))
      ) {
        errors[key] = '请至少选择一个知识库。'
      }
    } else if (
      typeof value !== 'number' ||
      !Number.isInteger(value) ||
      (min !== undefined && value < min) ||
      (max !== undefined && value > max)
    ) {
      errors[key] =
        `请填写允许范围内的整数${min !== undefined && max !== undefined ? `（${min}–${max}）` : ''}。`
    }
  }
  return errors
}

export function buildParams(values: JobFormValues): Record<string, number | boolean | string[]> {
  return Object.fromEntries(
    Object.entries(FORM_FIELDS[values.taskType] ?? {}).map(([field, key]) => [field, values[key]]),
  )
}
