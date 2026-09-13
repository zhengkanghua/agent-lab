/* 任务页的浏览器夹具：仅在内存受理，不模拟真实 Worker/Redis 的可靠性。 */
import { randomUUID } from 'node:crypto'
import { makeTaskRun, taskPolicy } from '../src/api/tasks.fixture.ts'
import { taskTypes } from '../src/api/scheduled-jobs.fixture.ts'

const jobId = '40000000-0000-4000-8000-000000000001'
const stamp = new Date().toISOString()
let policy = { ...taskPolicy }
const policyChanges = []
const jobs = new Map([
  [
    jobId,
    {
      id: jobId,
      key: 'sync-news',
      task_type: 'freshrss_sync',
      cron_expr: '0 * * * *',
      params: { limit_per_source: 2 },
      enabled: true,
      next_run_at: stamp,
      last_run: null,
      active_run: null,
      created_at: stamp,
      updated_at: stamp,
    },
  ],
])
const runs = new Map(
  [
    makeTaskRun({
      status: 'waiting_resource',
      attempts: 0,
      started_at: null,
      wait_reason: '索引写资源正在使用，稍后继续检查',
      task_type: 'document_processing',
      trigger_type: 'business',
    }),
    makeTaskRun({
      id: '50000000-0000-4000-8000-000000000002',
      task_type: 'pipeline_run_once',
      stats: {
        ok: false,
        sync: { synchronized_document_count: 12, failed_source_count: 1 },
        index: {
          parsed_document_count: 8,
          review_document_count: 3,
          indexed_document_count: 4,
          failed_document_count: 1,
        },
      },
    }),
    makeTaskRun({
      id: '50000000-0000-4000-8000-000000000003',
      job_id: null,
      source_job_id: jobId,
      status: 'failed',
      error_type: 'FreshRSSConnectionError',
    }),
  ].map((run) => [run.id, run]),
)
const receipts = new Map()
const json = (body, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})
const error = (code, detail, status = 409, run_id = null) =>
  json({ code, detail, retryable: false, run_id }, status)
const missing = () => error('task_run_not_found', '记录不存在', 404)

export function matchTaskApi(path, query, method, body, requestKey) {
  if (path === '/scheduled-jobs/task-types') return json(taskTypes)
  if (path === '/scheduled-jobs/validate-cron')
    return json({
      next_run_times: [stamp],
      next_run_times_local: [stamp],
      timezone: 'Asia/Shanghai',
    })
  if (path === '/task-policy/changes') return json(policyChanges)
  if (path === '/task-policy') {
    if (method === 'PUT') {
      policyChanges.unshift({
        id: randomUUID(),
        changed_at: new Date().toISOString(),
        actor: 'account:mock',
        previous: policy,
        current: { ...body },
      })
      policy = { ...body }
    }
    return json(policy)
  }
  if (path === '/task-runs' && method === 'GET') {
    const records = [...runs.values()]
      .reverse()
      .filter(
        (run) =>
          (!query.get('status') || query.get('status') === run.status) &&
          (!query.get('task_type') || query.get('task_type') === run.task_type),
      )
    const start = Number(query.get('offset') ?? 0)
    return json(records.slice(start, start + Number(query.get('limit') ?? 20)))
  }
  if (path === '/scheduled-jobs') {
    if (method === 'POST') {
      const job = {
        ...jobs.values().next().value,
        ...body,
        id: randomUUID(),
        last_run: null,
        active_run: null,
        created_at: stamp,
        updated_at: stamp,
      }
      jobs.set(job.id, job)
      return json(job, 201)
    }
    return json([...jobs.values()])
  }
  if (
    method === 'POST' &&
    (path.endsWith('/trigger') ||
      /^\/task-runs\/[^/]+\/retry$/.test(path) ||
      path === '/pipeline/run-once')
  ) {
    const identity = `${path}:${requestKey}`
    const previous = receipts.get(identity)
    if (previous)
      return previous.body === JSON.stringify(body)
        ? json(previous.receipt, 202)
        : error('task_request_conflict', '同一标识不能更换内容')
    const source = path.endsWith('/trigger') ? jobs.get(path.split('/')[2]) : null
    const failed = path.endsWith('/retry') ? runs.get(path.split('/')[2]) : null
    if (path.endsWith('/trigger') && !source) return missing()
    if (source?.active_run)
      return error('scheduled_job_already_running', '已有未结束执行', 409, source.active_run.id)
    if (path.endsWith('/retry') && !failed?.can_retry)
      return error('task_retry_unavailable', '只有失败执行可以重试')
    const run = makeTaskRun({
      id: randomUUID(),
      accepted_at: new Date().toISOString(),
      status: 'queued',
      task_type: source?.task_type ?? failed?.task_type ?? 'pipeline_run_once',
      job_id: source?.id ?? failed?.job_id ?? null,
      source_job_id: source?.id ?? failed?.source_job_id ?? null,
      config_snapshot: failed?.config_snapshot ?? { params: source?.params ?? body },
      policy_snapshot: { ...policy },
      retry_of: failed?.id ?? null,
      trigger_type: source ? 'manual' : failed ? 'retry' : 'direct',
    })
    runs.set(run.id, run)
    if (source) source.active_run = run
    const receipt = {
      run_id: run.id,
      job_id: run.job_id,
      status: run.status,
      details_expired: false,
    }
    receipts.set(identity, { body: JSON.stringify(body), receipt })
    return json(receipt, 202)
  }
  if (/^\/task-runs\/[^/]+/.test(path)) {
    const run = runs.get(path.split('/')[2])
    if (!run) return missing()
    if (path.endsWith('/cancel')) {
      if (!run.can_cancel) return error('task_operation_conflict', '当前不能取消', 409, run.id)
      Object.assign(run, {
        status: 'cancelled',
        can_cancel: false,
        finished_at: new Date().toISOString(),
      })
      const job = jobs.get(run.job_id)
      if (job?.active_run?.id === run.id) job.active_run = null
    }
    return json(run)
  }
  if (/^\/scheduled-jobs\/[^/]+/.test(path)) {
    const id = path.split('/')[2]
    if (path.endsWith('/runs'))
      return json([...runs.values()].filter((run) => run.source_job_id === id))
    const job = jobs.get(id)
    if (!job) return missing()
    if (method === 'PATCH') Object.assign(job, body)
    if (method === 'DELETE') {
      jobs.delete(id)
      for (const run of runs.values()) if (run.job_id === id) run.job_id = null
      return { status: 204, contentType: 'text/plain', body: '' }
    }
    return json(job)
  }
  return null
}
