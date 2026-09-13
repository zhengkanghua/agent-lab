import type { components } from './generated/openapi'
import { ApiError, requestJson, requestVoid } from './client'
import { hasText, isRecord, isStringArray, isUuid } from './json-guards'
import {
  acceptTask,
  isJobRunDto,
  isIsoDateTime,
  isIsoDateTimeOrNull,
  type TaskAcceptedDto,
} from './tasks'
export { isJobRunDto } from './tasks'

export type ScheduledJobDto = components['schemas']['ScheduledJobResponse']
export type JobRunDto = components['schemas']['JobRunResponse']
export type ScheduledJobCreateRequest = components['schemas']['ScheduledJobCreateRequest']
export type ScheduledJobUpdateRequest = components['schemas']['ScheduledJobUpdateRequest']
export type CronValidationDto = components['schemas']['CronValidateResponse']
export type ScheduledJobTriggerDto = TaskAcceptedDto

export type ScheduledJobTaskType = string
export type ScheduledTaskTypeDto = components['schemas']['ScheduledTaskTypeResponse']

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

export async function listScheduledTaskTypes(
  signal?: AbortSignal,
): Promise<ScheduledTaskTypeDto[]> {
  const response = await requestJson<unknown>('/scheduled-jobs/task-types', {
    method: 'GET',
    signal,
  })
  if (
    !Array.isArray(response) ||
    !response.every(
      (value) =>
        isRecord(value) &&
        hasText(value.task_type) &&
        hasText(value.description) &&
        isRecord(value.defaults) &&
        isRecord(value.params_schema) &&
        typeof value.schedulable === 'boolean',
    )
  ) {
    throw invalidSchedulerResponse('定时任务接口返回了无效的任务类型。')
  }
  return response as ScheduledTaskTypeDto[]
}

export async function getScheduledJobRun(
  jobId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<JobRunDto> {
  const response = await requestJson<unknown>(
    `/scheduled-jobs/${encodeURIComponent(jobId)}/runs/${encodeURIComponent(runId)}`,
    { method: 'GET', signal },
  )
  if (!isJobRunDto(response) || response.id !== runId || response.job_id !== jobId)
    throw invalidSchedulerResponse('任务执行记录无效。')
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

export function triggerScheduledJob(
  jobId: string,
  requestKey: string,
): Promise<ScheduledJobTriggerDto> {
  return acceptTask(`/scheduled-jobs/${encodeURIComponent(jobId)}/trigger`, requestKey)
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
    (response.timezone !== undefined && !hasText(response.timezone)) ||
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
    hasText(value.task_type) &&
    hasText(value.cron_expr) &&
    isRecord(value.params) &&
    typeof value.enabled === 'boolean' &&
    isIsoDateTimeOrNull(value.next_run_at) &&
    (value.last_run === null || isJobRunDto(value.last_run)) &&
    (value.active_run === undefined ||
      value.active_run === null ||
      isJobRunDto(value.active_run)) &&
    isIsoDateTime(value.created_at) &&
    isIsoDateTime(value.updated_at)
  )
}

function invalidSchedulerResponse(message: string): ApiError {
  return new ApiError({ message, code: 'response_invalid' })
}
