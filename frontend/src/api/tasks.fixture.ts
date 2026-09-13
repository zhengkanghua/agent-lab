import type { JobRunDto, TaskPolicy } from './tasks'

export const taskPolicy: TaskPolicy = {
  max_retries: 3,
  retry_delay_seconds: 30,
  history_retention_days: 30,
}

/** 完整的合成 HTTP 详情，页面与接口测试共用；不代表已连接真实 Worker。 */
export function makeTaskRun(overrides: Partial<JobRunDto> = {}): JobRunDto {
  const status = overrides.status ?? 'succeeded'
  return {
    id: '50000000-0000-4000-8000-000000000001',
    job_id: null,
    source_job_id: null,
    task_type: 'freshrss_sync',
    task_version: 1,
    trigger_type: 'manual',
    actor: 'account:test',
    status,
    accepted_at: '2026-09-02T04:00:00Z',
    scheduled_for: null,
    started_at: status === 'queued' ? null : '2026-09-02T04:00:00Z',
    finished_at: status === 'succeeded' || status === 'failed' ? '2026-09-02T04:01:00Z' : null,
    available_at: '2026-09-02T04:00:00Z',
    expires_at: '2026-10-02T04:01:00Z',
    attempts: status === 'queued' ? 0 : 1,
    delivery_count: 1,
    last_dispatched_at: '2026-09-02T04:00:00Z',
    dispatch_error_type: null,
    heartbeat_at: null,
    owner: null,
    wait_reason: null,
    error_type: null,
    stats: {},
    config_snapshot: { task_type: 'freshrss_sync', params: { limit_per_source: 2 } },
    policy_snapshot: { ...taskPolicy },
    recovery: {},
    retry_of: null,
    needs_attention: status === 'needs_attention',
    can_cancel: ['queued', 'waiting_resource', 'retry_wait'].includes(status),
    can_retry: status === 'failed',
    ...overrides,
  }
}
