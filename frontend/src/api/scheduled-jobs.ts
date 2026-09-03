import type { components } from './generated/openapi'
import { ApiError, requestJson, requestVoid } from './client'
import { hasText, isRecord, isStringArray, isUuid } from './json-guards'

export type ScheduledJobDto = components['schemas']['ScheduledJobResponse']
export type JobRunDto = components['schemas']['JobRunResponse']
export type ScheduledJobCreateRequest = components['schemas']['ScheduledJobCreateRequest']
export type ScheduledJobUpdateRequest = components['schemas']['ScheduledJobUpdateRequest']
export type CronValidationDto = components['schemas']['CronValidateResponse']
export type ScheduledJobTriggerDto = components['schemas']['ScheduledJobTriggerResponse']

/** 任务类型清单与后端注册表一一对应；新类型要两边一起加。 */
export const SCHEDULED_JOB_TASK_TYPES = ['freshrss_sync', 'index_pending'] as const
export type ScheduledJobTaskType = (typeof SCHEDULED_JOB_TASK_TYPES)[number]

export const SCHEDULED_JOB_RUN_STATUSES = ['running', 'succeeded', 'failed', 'skipped'] as const
export type ScheduledJobRunStatus = (typeof SCHEDULED_JOB_RUN_STATUSES)[number]

export const SCHEDULED_JOB_TRIGGER_TYPES = ['scheduled', 'manual'] as const
export type ScheduledJobTriggerType = (typeof SCHEDULED_JOB_TRIGGER_TYPES)[number]

export interface CreateScheduledJobOptions {
  key: string
  taskType: ScheduledJobTaskType
  cronExpr: string
  params: Record<string, unknown>
  enabled: boolean
}

export interface UpdateScheduledJobOptions {
  jobId: string
  cronExpr?: string
  params?: Record<string, unknown>
  enabled?: boolean
}

export interface ValidateCronOptions {
  cronExpr: string
}

export async function listScheduledJobs(signal?: AbortSignal): Promise<ScheduledJobDto[]> {
  const response = await requestJson<unknown>('/scheduled-jobs', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isScheduledJobDto)) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的任务列表。')
  }
  return response
}

export async function createScheduledJob(
  options: CreateScheduledJobOptions,
): Promise<ScheduledJobDto> {
  const payload: ScheduledJobCreateRequest = {
    key: options.key,
    task_type: options.taskType,
    cron_expr: options.cronExpr,
    params: options.params,
    enabled: options.enabled,
  }
  return requestScheduledJob('/scheduled-jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateScheduledJob(
  options: UpdateScheduledJobOptions,
): Promise<ScheduledJobDto> {
  const payload: ScheduledJobUpdateRequest = {
    ...(options.cronExpr === undefined ? {} : { cron_expr: options.cronExpr }),
    ...(options.params === undefined ? {} : { params: options.params }),
    ...(options.enabled === undefined ? {} : { enabled: options.enabled }),
  }
  return requestScheduledJob(`/scheduled-jobs/${encodeURIComponent(options.jobId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteScheduledJob(jobId: string): Promise<void> {
  await requestVoid(`/scheduled-jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

export async function triggerScheduledJob(jobId: string): Promise<ScheduledJobTriggerDto> {
  const response = await requestJson<unknown>(
    `/scheduled-jobs/${encodeURIComponent(jobId)}/trigger`,
    { method: 'POST' },
  )
  if (
    !isRecord(response) ||
    !isUuid(response.job_id) ||
    !isUuid(response.run_id) ||
    response.status !== 'running'
  ) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的触发回执。')
  }
  return response as unknown as ScheduledJobTriggerDto
}

export async function listScheduledJobRuns(
  jobId: string,
  limit: number,
  signal?: AbortSignal,
): Promise<JobRunDto[]> {
  const query = new URLSearchParams({ limit: String(limit) })
  const response = await requestJson<unknown>(
    `/scheduled-jobs/${encodeURIComponent(jobId)}/runs?${query.toString()}`,
    { method: 'GET', signal },
  )
  if (!Array.isArray(response) || !response.every(isJobRunDto)) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的执行历史。')
  }
  return response
}

export async function validateCron(
  options: ValidateCronOptions & { signal?: AbortSignal },
): Promise<CronValidationDto> {
  const response = await requestJson<unknown>('/scheduled-jobs/validate-cron', {
    method: 'POST',
    body: JSON.stringify({ cron_expr: options.cronExpr }),
    signal: options.signal,
  })
  if (
    !isRecord(response) ||
    !isStringArray(response.next_run_times) ||
    !isStringArray(response.next_run_times_local) ||
    response.next_run_times.length === 0 ||
    response.next_run_times.length !== response.next_run_times_local.length
  ) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的 cron 预览。')
  }
  return response as unknown as CronValidationDto
}

async function requestScheduledJob(path: string, init: RequestInit): Promise<ScheduledJobDto> {
  const response = await requestJson<unknown>(path, init)
  if (!isScheduledJobDto(response)) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的任务。')
  }
  return response
}

export function isScheduledJobDto(value: unknown): value is ScheduledJobDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.key) &&
    isTaskType(value.task_type) &&
    hasText(value.cron_expr) &&
    isRecord(value.params) &&
    typeof value.enabled === 'boolean' &&
    isIsoDateTimeOrNull(value.next_run_at) &&
    (value.last_run === null || isJobRunDto(value.last_run)) &&
    isIsoDateTime(value.created_at) &&
    isIsoDateTime(value.updated_at)
  )
}

export function isJobRunDto(value: unknown): value is JobRunDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    isUuid(value.job_id) &&
    isTriggerType(value.trigger_type) &&
    isRunStatus(value.status) &&
    isIsoDateTime(value.started_at) &&
    isIsoDateTimeOrNull(value.finished_at) &&
    isRecord(value.stats) &&
    (value.error_type === null || hasText(value.error_type))
  )
}

function isTaskType(value: unknown): value is ScheduledJobTaskType {
  return (
    typeof value === 'string' && (SCHEDULED_JOB_TASK_TYPES as readonly string[]).includes(value)
  )
}

function isRunStatus(value: unknown): value is ScheduledJobRunStatus {
  return (
    typeof value === 'string' && (SCHEDULED_JOB_RUN_STATUSES as readonly string[]).includes(value)
  )
}

function isTriggerType(value: unknown): value is ScheduledJobTriggerType {
  return (
    typeof value === 'string' && (SCHEDULED_JOB_TRIGGER_TYPES as readonly string[]).includes(value)
  )
}

function isIsoDateTime(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && !Number.isNaN(Date.parse(value))
}

function isIsoDateTimeOrNull(value: unknown): value is string | null {
  return value === null || isIsoDateTime(value)
}

function invalidSchedulerResponse(message: string): ApiError {
  return new ApiError({ message, code: 'response_invalid' })
}
