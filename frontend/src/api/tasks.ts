import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isRecord, isUuid } from './json-guards'

export type JobRunDto = components['schemas']['JobRunResponse']
export type TaskAcceptedDto = components['schemas']['TaskAcceptedResponse']
export type TaskPolicy = components['schemas']['ExecutionPolicy']
export type TaskPolicyChange = components['schemas']['TaskPolicyChangeResponse']
export type PipelineRequest = components['schemas']['PipelineRunOnceRequest']
export type RunStatus = JobRunDto['status']

export const RUN_STATUSES = [
  'queued',
  'waiting_resource',
  'running',
  'retry_wait',
  'needs_attention',
  'succeeded',
  'failed',
  'cancelled',
  'skipped',
] as const satisfies readonly RunStatus[]

export function isTerminalStatus(status: RunStatus): boolean {
  return ['succeeded', 'failed', 'cancelled', 'skipped'].includes(status)
}

export function isIsoDateTime(value: unknown): value is string {
  return hasText(value) && !Number.isNaN(Date.parse(value))
}

export function isIsoDateTimeOrNull(value: unknown): value is string | null {
  return value === null || isIsoDateTime(value)
}

function isPolicy(value: unknown): value is TaskPolicy {
  return (
    isRecord(value) &&
    ['max_retries', 'retry_delay_seconds', 'history_retention_days'].every((key) =>
      Number.isInteger(value[key]),
    )
  )
}

/** 只校验页面会消费的契约边界；业务统计保留原始结构，交由显式展示适配。 */
export function isJobRunDto(value: unknown): value is JobRunDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    (value.job_id === null || isUuid(value.job_id)) &&
    (value.source_job_id === null || isUuid(value.source_job_id)) &&
    hasText(value.task_type) &&
    hasText(value.trigger_type) &&
    RUN_STATUSES.some((status) => status === value.status) &&
    isIsoDateTime(value.accepted_at) &&
    isIsoDateTime(value.available_at) &&
    isIsoDateTimeOrNull(value.started_at) &&
    isIsoDateTimeOrNull(value.finished_at) &&
    isIsoDateTimeOrNull(value.scheduled_for) &&
    isIsoDateTimeOrNull(value.expires_at) &&
    Number.isInteger(value.attempts) &&
    Number.isInteger(value.delivery_count) &&
    (value.error_type === null || hasText(value.error_type)) &&
    (value.wait_reason === null || hasText(value.wait_reason)) &&
    (value.retry_of === null || isUuid(value.retry_of)) &&
    isRecord(value.stats) &&
    isRecord(value.config_snapshot) &&
    isRecord(value.recovery) &&
    isPolicy(value.policy_snapshot) &&
    typeof value.can_cancel === 'boolean' &&
    typeof value.can_retry === 'boolean' &&
    typeof value.needs_attention === 'boolean'
  )
}

export async function getTaskRun(runId: string, signal?: AbortSignal): Promise<JobRunDto> {
  const run = await requestRun(`/task-runs/${encodeURIComponent(runId)}`, { method: 'GET', signal })
  if (run.id !== runId) throw invalidResponse()
  return run
}

export async function listTaskRuns(
  options: { offset: number; limit: number; status?: RunStatus; taskType?: string },
  signal?: AbortSignal,
): Promise<JobRunDto[]> {
  const query = new URLSearchParams({
    offset: String(options.offset),
    limit: String(options.limit),
  })
  if (options.status) query.set('status', options.status)
  if (options.taskType) query.set('task_type', options.taskType)
  const response = await requestJson<unknown>(`/task-runs?${query}`, { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isJobRunDto)) throw invalidResponse()
  return response
}

export async function acceptTask(
  path: string,
  requestKey: string,
  body?: unknown,
): Promise<TaskAcceptedDto> {
  const response = await requestJson<unknown>(path, {
    method: 'POST',
    headers: { 'Idempotency-Key': requestKey },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (
    !isRecord(response) ||
    !isUuid(response.run_id) ||
    !(response.status === 'expired' || RUN_STATUSES.some((status) => status === response.status)) ||
    typeof response.details_expired !== 'boolean'
  )
    throw invalidResponse()
  return response as TaskAcceptedDto
}

export function retryTask(runId: string, requestKey: string): Promise<TaskAcceptedDto> {
  return acceptTask(`/task-runs/${encodeURIComponent(runId)}/retry`, requestKey)
}

export function submitPipeline(
  params: PipelineRequest,
  requestKey: string,
): Promise<TaskAcceptedDto> {
  return acceptTask('/pipeline/run-once', requestKey, params)
}

export function cancelTask(runId: string): Promise<JobRunDto> {
  return requestRun(`/task-runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

export async function getTaskPolicy(signal?: AbortSignal): Promise<TaskPolicy> {
  const value = await requestJson<unknown>('/task-policy', { method: 'GET', signal })
  if (!isPolicy(value)) throw invalidResponse()
  return value
}

export async function updateTaskPolicy(policy: TaskPolicy): Promise<TaskPolicy> {
  const value = await requestJson<unknown>('/task-policy', {
    method: 'PUT',
    body: JSON.stringify(policy),
  })
  if (!isPolicy(value)) throw invalidResponse()
  return value
}

export async function getTaskPolicyChanges(signal?: AbortSignal): Promise<TaskPolicyChange[]> {
  const value = await requestJson<unknown>('/task-policy/changes', { method: 'GET', signal })
  if (
    !Array.isArray(value) ||
    !value.every(
      (item) =>
        isRecord(item) &&
        isUuid(item.id) &&
        hasText(item.actor) &&
        isIsoDateTime(item.changed_at) &&
        isPolicy(item.previous) &&
        isPolicy(item.current),
    )
  )
    throw invalidResponse()
  return value as TaskPolicyChange[]
}

async function requestRun(path: string, init: RequestInit): Promise<JobRunDto> {
  const value = await requestJson<unknown>(path, init)
  if (!isJobRunDto(value)) throw invalidResponse()
  return value
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: '任务接口返回了无法识别的响应，请重新查询。',
    code: 'response_invalid',
  })
}
