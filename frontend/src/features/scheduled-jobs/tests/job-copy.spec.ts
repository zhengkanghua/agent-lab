import { describe, expect, it } from 'vitest'
import type { JobRunDto, ScheduledJobDto } from '@/api/scheduled-jobs'
import { formatBeijingTime, formatLastRunSummary, formatRunStats } from '../model/job-copy'

const jobBase = {
  id: '40000000-0000-4000-8000-000000000001',
  key: 'freshrss-sync',
  task_type: 'freshrss_sync',
  cron_expr: '*/10 * * * *',
  params: {},
  enabled: true,
  next_run_at: null,
  last_run: null,
  created_at: '2026-09-02T00:00:00Z',
  updated_at: '2026-09-02T00:00:00Z',
} as unknown as ScheduledJobDto

function makeRun(overrides: Partial<JobRunDto>): JobRunDto {
  return {
    id: '50000000-0000-4000-8000-000000000001',
    job_id: jobBase.id,
    trigger_type: 'manual',
    status: 'succeeded',
    started_at: '2026-09-02T04:00:00Z',
    finished_at: '2026-09-02T04:01:00Z',
    stats: {},
    error_type: null,
    ...overrides,
  }
}

describe('formatBeijingTime', () => {
  it('converts UTC instants to fixed Beijing display format', () => {
    // UTC 01:00 = 北京 09:00（Q6 共识：固定 YYYY-MM-DD HH:mm:ss，不做相对时间）。
    expect(formatBeijingTime('2026-09-03T01:00:00Z')).toBe('2026-09-03 09:00:00')
  })

  it('handles the day boundary across timezones', () => {
    expect(formatBeijingTime('2026-09-03T17:30:05Z')).toBe('2026-09-04 01:30:05')
  })

  it('returns unparseable input as-is instead of Invalid Date', () => {
    expect(formatBeijingTime('not-a-date')).toBe('not-a-date')
  })
})

describe('formatLastRunSummary', () => {
  it('says 尚未执行 when there is no run', () => {
    expect(formatLastRunSummary(jobBase)).toBe('尚未执行')
  })

  it('summarizes status and Beijing finish time', () => {
    const job = {
      ...jobBase,
      last_run: makeRun({ status: 'failed', error_type: 'OllamaTimeoutError' }),
    } as unknown as ScheduledJobDto
    const summary = formatLastRunSummary(job)
    expect(summary).toContain('失败')
    expect(summary).toContain('2026-09-02 12:01:00')
  })
})

describe('formatRunStats', () => {
  it('renders skipped reason verbatim', () => {
    const run = makeRun({
      status: 'skipped',
      finished_at: null,
      stats: { reason: 'previous_run_still_running' },
    })
    expect(formatRunStats(run)).toBe('上一轮尚未结束，本轮按策略跳过')
  })

  it('renders sync counts and aggregated failures', () => {
    const run = makeRun({
      status: 'succeeded',
      stats: {
        source_count: 2,
        synchronized_document_count: 3,
        failed_source_count: 1,
        failures: { FreshRSSConnectionError: 1 },
      },
    })
    const summary = formatRunStats(run)
    expect(summary).toContain('同步文档 3')
    expect(summary).toContain('失败来源 1')
    expect(summary).toContain('失败：FreshRSSConnectionError×1')
  })

  it('renders index counts including stale requeues', () => {
    const run = makeRun({
      stats: {
        candidate_count: 5,
        requeued_stale_count: 2,
        indexed_count: 3,
        failures: {},
      },
    })
    const summary = formatRunStats(run)
    expect(summary).toContain('已索引 3')
    expect(summary).toContain('候选 5')
    expect(summary).toContain('回收超时 2')
  })

  it('falls back to 本轮无变更 for empty stats', () => {
    expect(formatRunStats(makeRun({ stats: {} }))).toBe('本轮无变更')
  })
})
