import { SCHEDULED_JOB_TASK_TYPES } from '@/api/scheduled-jobs'

/*
 * 表单校验与参数边界。
 *
 * 边界数值与后端 pipeline/limits.py 的常量保持一致（那边是权威）：这里复制一份是
 * 刻意的——表单要在提交前给出即时的取值范围提示，而不是等后端 422 才知道。
 * 后端改边界时两边一起改；就算这边漏改，后端 422 + 错误文案仍会兜住，不会写坏数据。
 */

export const KEY_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/
export const KEY_MIN_LENGTH = 3
export const KEY_MAX_LENGTH = 64

export const LIMIT_PER_SOURCE_MIN = 1
export const LIMIT_PER_SOURCE_MAX = 100
export const LIMIT_PER_SOURCE_DEFAULT = 2

export const BATCH_SIZE_MIN = 1
export const BATCH_SIZE_MAX = 1000
export const BATCH_SIZE_DEFAULT = 20

export const STALE_AFTER_MINUTES_MIN = 1
export const STALE_AFTER_MINUTES_MAX = 10080
export const STALE_AFTER_MINUTES_DEFAULT = 60

export interface JobFormValues {
  key: string
  taskType: string
  cronExpr: string
  limitPerSource: number
  batchSize: number
  staleAfterMinutes: number
  enabled: boolean
}

/** 字段名 → 错误文案；空 record 表示整表通过。 */
export type JobFormErrors = Partial<Record<keyof JobFormValues | 'cron', string>>

export function validateKey(key: string): string {
  if (key.trim().length === 0) return '请填写任务标识。'
  if (key.length < KEY_MIN_LENGTH || key.length > KEY_MAX_LENGTH) {
    return `任务标识需要 ${KEY_MIN_LENGTH}–${KEY_MAX_LENGTH} 个字符。`
  }
  if (!KEY_PATTERN.test(key)) {
    return '任务标识只能用小写字母、数字和短横线（不能以短横线开头或结尾）。'
  }
  return ''
}

/** 客户端只做形状检查（5 段非空）；语义对不对交给后端 validate-cron 预览判定。 */
export function validateCronShape(cronExpr: string): string {
  const fields = cronExpr.trim().split(/\s+/)
  if (fields.length !== 5 || fields.some((field) => field.length === 0)) {
    return 'cron 表达式需要 5 段（分 时 日 月 周），用空格分隔。'
  }
  return ''
}

export function validateParams(values: JobFormValues): JobFormErrors {
  const errors: JobFormErrors = {}
  if (!SCHEDULED_JOB_TASK_TYPES.includes(values.taskType as never)) {
    errors.taskType = '请选择任务类型。'
  }
  const keyError = validateKey(values.key)
  if (keyError) errors.key = keyError
  const cronError = validateCronShape(values.cronExpr)
  if (cronError) errors.cron = cronError

  if (values.taskType === 'freshrss_sync') {
    if (
      !Number.isInteger(values.limitPerSource) ||
      values.limitPerSource < LIMIT_PER_SOURCE_MIN ||
      values.limitPerSource > LIMIT_PER_SOURCE_MAX
    ) {
      errors.limitPerSource = `每个来源最多同步 ${LIMIT_PER_SOURCE_MIN}–${LIMIT_PER_SOURCE_MAX} 篇。`
    }
  }
  if (values.taskType === 'index_pending') {
    if (
      !Number.isInteger(values.batchSize) ||
      values.batchSize < BATCH_SIZE_MIN ||
      values.batchSize > BATCH_SIZE_MAX
    ) {
      errors.batchSize = `单批索引 ${BATCH_SIZE_MIN}–${BATCH_SIZE_MAX} 篇。`
    }
    if (
      !Number.isInteger(values.staleAfterMinutes) ||
      values.staleAfterMinutes < STALE_AFTER_MINUTES_MIN ||
      values.staleAfterMinutes > STALE_AFTER_MINUTES_MAX
    ) {
      errors.staleAfterMinutes = `回收阈值 ${STALE_AFTER_MINUTES_MIN}–${STALE_AFTER_MINUTES_MAX} 分钟。`
    }
  }
  return errors
}

/** 按任务类型把表单值收敛成后端 params 对象（多余字段不提交）。 */
export function buildParams(values: JobFormValues): Record<string, number> {
  if (values.taskType === 'freshrss_sync') {
    return { limit_per_source: values.limitPerSource }
  }
  return { batch_size: values.batchSize, stale_after_minutes: values.staleAfterMinutes }
}
