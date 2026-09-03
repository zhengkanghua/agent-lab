import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createScheduledJob,
  deleteScheduledJob,
  listScheduledJobRuns,
  listScheduledJobs,
  triggerScheduledJob,
  updateScheduledJob,
  validateCron,
} from './scheduled-jobs'

const job = {
  id: '40000000-0000-4000-8000-000000000001',
  key: 'freshrss-sync',
  task_type: 'freshrss_sync',
  cron_expr: '*/10 * * * *',
  params: { limit_per_source: 2 },
  enabled: true,
  next_run_at: '2026-09-03T01:00:00Z',
  last_run: null,
  created_at: '2026-09-02T00:00:00Z',
  updated_at: '2026-09-02T00:00:00Z',
}

const run = {
  id: '50000000-0000-4000-8000-000000000001',
  job_id: job.id,
  trigger_type: 'manual',
  status: 'succeeded',
  started_at: '2026-09-02T04:00:00Z',
  finished_at: '2026-09-02T04:01:00Z',
  stats: { synchronized_document_count: 3, failures: {} },
  error_type: null,
}

function jsonResponse(body: unknown, status = 200): Response {
  if (body === undefined) return new Response(null, { status })
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('scheduled jobs API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists validated jobs through the superuser endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([job]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listScheduledJobs()).resolves.toEqual([job])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/scheduled-jobs',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    )
  })

  it('maps create, update, trigger, runs, and cron-preview bodies exactly', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(job, 201))
      .mockResolvedValueOnce(jsonResponse({ ...job, enabled: false }))
      .mockResolvedValueOnce(jsonResponse({ job_id: job.id, run_id: run.id, status: 'running' }))
      .mockResolvedValueOnce(jsonResponse([run]))
      .mockResolvedValueOnce(
        jsonResponse({
          next_run_times: ['2026-09-03T01:00:00Z'],
          next_run_times_local: ['2026-09-03T09:00:00+08:00'],
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await createScheduledJob({
      key: 'freshrss-sync',
      taskType: 'freshrss_sync',
      cronExpr: '*/10 * * * *',
      params: { limit_per_source: 2 },
      enabled: true,
    })
    await updateScheduledJob({ jobId: job.id, enabled: false })
    await triggerScheduledJob(job.id)
    await listScheduledJobRuns(job.id, 20)
    await validateCron({ cronExpr: '0 9 * * *' })

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/scheduled-jobs',
      `/api/scheduled-jobs/${job.id}`,
      `/api/scheduled-jobs/${job.id}/trigger`,
      `/api/scheduled-jobs/${job.id}/runs?limit=20`,
      '/api/scheduled-jobs/validate-cron',
    ])
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).body).toBe(
      JSON.stringify({
        key: 'freshrss-sync',
        task_type: 'freshrss_sync',
        cron_expr: '*/10 * * * *',
        params: { limit_per_source: 2 },
        enabled: true,
      }),
    )
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).body).toBe(
      JSON.stringify({ enabled: false }),
    )
    expect(fetchMock.mock.calls[2]?.[1]).toEqual(expect.objectContaining({ method: 'POST' }))
  })

  it('deletes with no body and tolerates an empty 204 response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(undefined, 204))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteScheduledJob(job.id)).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/scheduled-jobs/${job.id}`,
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('rejects malformed jobs, runs, receipts, and cron previews before rendering', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([{ ...job, task_type: 'mystery_type' }]))
      .mockResolvedValueOnce(jsonResponse([{ ...run, status: 'unknown' }]))
      .mockResolvedValueOnce(jsonResponse({ job_id: job.id, run_id: run.id, status: 'done' }))
      .mockResolvedValueOnce(jsonResponse({ next_run_times: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listScheduledJobs()).rejects.toMatchObject({ code: 'response_invalid' })
    await expect(listScheduledJobRuns(job.id, 20)).rejects.toMatchObject({
      code: 'response_invalid',
    })
    await expect(triggerScheduledJob(job.id)).rejects.toMatchObject({ code: 'response_invalid' })
    await expect(validateCron({ cronExpr: '* * * * *' })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })
})
