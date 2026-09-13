import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getTaskRun,
  listTaskRuns,
  retryTask,
  submitPipeline,
  cancelTask,
  getTaskPolicy,
  updateTaskPolicy,
} from './tasks'
import { makeTaskRun, taskPolicy } from './tasks.fixture'

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
afterEach(() => vi.unstubAllGlobals())

describe('shared task HTTP contract', () => {
  it('queries a queued one-time execution without a configuration and preserves wait states', async () => {
    const run = makeTaskRun({ status: 'queued', job_id: null, started_at: null })
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response(run))
      .mockResolvedValueOnce(response([run]))
    vi.stubGlobal('fetch', fetch)
    await expect(getTaskRun(run.id)).resolves.toEqual(run)
    await expect(
      listTaskRuns({ offset: 20, limit: 20, status: 'waiting_resource' }),
    ).resolves.toEqual([run])
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      `/api/task-runs/${run.id}`,
      '/api/task-runs?offset=20&limit=20&status=waiting_resource',
    ])
  })

  it('sends the caller request key and exact Pipeline body, and accepts expired receipts', async () => {
    const run = makeTaskRun()
    const params = { limit_per_source: 2, batch_size: 20, stale_after_minutes: 60 }
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        response({ run_id: run.id, status: 'queued', details_expired: false }, 202),
      )
      .mockResolvedValueOnce(
        response({ run_id: run.id, status: 'expired', details_expired: true }, 202),
      )
    vi.stubGlobal('fetch', fetch)
    await submitPipeline(params, 'original-pipeline-request')
    await expect(retryTask(run.id, 'original-retry-request')).resolves.toMatchObject({
      run_id: run.id,
      details_expired: true,
    })
    expect(JSON.parse(fetch.mock.calls[0]?.[1].body)).toEqual(params)
    expect(new Headers(fetch.mock.calls[0]?.[1].headers).get('Idempotency-Key')).toBe(
      'original-pipeline-request',
    )
    expect(new Headers(fetch.mock.calls[1]?.[1].headers).get('Idempotency-Key')).toBe(
      'original-retry-request',
    )
  })

  it('keeps a conflict execution id and distinguishes expired details from missing records', async () => {
    const run = makeTaskRun()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(
          response(
            { code: 'scheduled_job_already_running', run_id: run.id, detail: '已有执行' },
            409,
          ),
        )
        .mockResolvedValueOnce(
          response({ code: 'task_run_expired', run_id: run.id, detail: '详情过期' }, 410),
        ),
    )
    await expect(retryTask(run.id, 'retry')).rejects.toMatchObject({ status: 409, runId: run.id })
    await expect(getTaskRun(run.id)).rejects.toMatchObject({ status: 410, runId: run.id })
  })

  it('maps cancellation and policy changes to their public endpoints', async () => {
    const run = makeTaskRun({ status: 'cancelled' })
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response(run))
      .mockResolvedValueOnce(response(taskPolicy))
      .mockResolvedValueOnce(response({ ...taskPolicy, max_retries: 0 }))
    vi.stubGlobal('fetch', fetch)
    await expect(cancelTask(run.id)).resolves.toMatchObject({
      status: 'cancelled',
      can_cancel: false,
    })
    await getTaskPolicy()
    await updateTaskPolicy({ ...taskPolicy, max_retries: 0 })
    expect(fetch.mock.calls.map((call) => [call[0], call[1].method])).toEqual([
      [`/api/task-runs/${run.id}/cancel`, 'POST'],
      ['/api/task-policy', 'GET'],
      ['/api/task-policy', 'PUT'],
    ])
  })
})
