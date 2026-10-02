import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createApp } from 'vue'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { ApiError } from '@/api/client'
import type { JobRunDto, ScheduledJobDto } from '@/api/scheduled-jobs'

/* 定时任务目录的编排（344 行、此前只被页面级 spec 间接覆盖）。挑的是几条
 * 「错了会重复提交、会卡住轮询、会粘住错误」的行为：
 *   - 待跟踪的执行能从 sessionStorage 恢复（刷新页面后仍等得到终态）；
 *   - 暂存内容损坏时不抛，页面照常可用（存储是加速，不是依赖）；
 *   - 终态到达后从暂存里清掉、回调一次，重复调用不再回调（防跨轮重复上报）；
 *   - 已在执行的任务不再重复触发（这是唯一会真的多跑一遍后台的入口）；
 *   - 受理成功走「待跟踪 + 展开历史」，被拒绝与「待核实」两种失败分开呈现。
 */

const api = vi.hoisted(() => ({
  createScheduledJob: vi.fn(),
  deleteScheduledJob: vi.fn(),
  listScheduledJobs: vi.fn(),
  listScheduledTaskTypes: vi.fn(),
  updateScheduledJob: vi.fn(),
  getTaskRun: vi.fn(),
}))

vi.mock('@/api/scheduled-jobs', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  ...api,
}))
// 只替掉取数，isTerminalStatus 用真的——它是被测逻辑的判据。
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getTaskRun: api.getTaskRun,
}))

import { useScheduledJobDirectory } from '../composables/useScheduledJobDirectory'
import type { TaskSubmissions } from '../composables/useTaskSubmissions'

const RECEIPT_KEY = 'scheduled-job-receipts:account-1'

function job(overrides: Partial<ScheduledJobDto> = {}): ScheduledJobDto {
  return {
    id: 'job-1',
    key: 'sync-news',
    task_type: 'freshrss_sync',
    cron_expr: '0 * * * *',
    enabled: true,
    params: {},
    active_run: null,
    last_run: null,
    next_run_at: null,
    ...overrides,
  } as unknown as ScheduledJobDto
}

function run(overrides: Partial<JobRunDto> = {}): JobRunDto {
  return {
    id: 'run-1',
    job_id: 'job-1',
    source_job_id: 'job-1',
    status: 'succeeded',
    needs_attention: false,
    stats: {},
    wait_reason: null,
    error_type: null,
    ...overrides,
  } as unknown as JobRunDto
}

function build() {
  const submit = vi.fn()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  let result: ReturnType<typeof useScheduledJobDirectory> | undefined

  const app = createApp({
    setup() {
      result = useScheduledJobDirectory({
        accountId: 'account-1',
        submissions: { submit } as unknown as TaskSubmissions,
      })
      return () => null
    },
  })
  app.use(VueQueryPlugin, { queryClient })
  app.mount(document.createElement('div'))

  return { dir: result!, stop: () => app.unmount(), submit }
}

describe('useScheduledJobDirectory', () => {
  beforeEach(() => {
    sessionStorage.clear()
    api.listScheduledJobs.mockReset().mockResolvedValue([job()])
    api.listScheduledTaskTypes.mockReset().mockResolvedValue([])
    api.getTaskRun.mockReset().mockResolvedValue(run())
  })

  afterEach(() => {
    sessionStorage.clear()
  })

  it('从 sessionStorage 恢复待跟踪的执行，刷新后仍继续等它', async () => {
    sessionStorage.setItem(RECEIPT_KEY, JSON.stringify([['run-1', 'job-1']]))
    // 还在跑：终态的会被立刻清出跟踪表，这里要验的是「恢复」这一步。
    api.getTaskRun.mockResolvedValue(run({ status: 'running' }))
    const { dir, stop } = build()

    await flushPromises()

    expect([...dir.awaitedRunIds.keys()]).toEqual(['run-1'])
    expect(api.getTaskRun).toHaveBeenCalledWith('run-1', expect.anything())
    stop()
  })

  it('暂存内容损坏时不抛，列表照常读出来', async () => {
    sessionStorage.setItem(RECEIPT_KEY, '{ 这不是 JSON')
    const { dir, stop } = build()

    await flushPromises()

    expect(dir.awaitedRunIds.size).toBe(0)
    expect(dir.jobs.value).toHaveLength(1)
    stop()
  })

  it('待跟踪的执行进入终态后从暂存清掉，并给出结果摘要', async () => {
    sessionStorage.setItem(RECEIPT_KEY, JSON.stringify([['run-1', 'job-1']]))
    const { dir, stop } = build()

    // 两轮微任务：先让列表读回来，再让被跟踪的那条执行读回来。
    await flushPromises()
    await flushPromises()

    expect(dir.awaitedRunIds.size).toBe(0)
    expect(sessionStorage.getItem(RECEIPT_KEY)).toBe('[]')
    // 结果落在全局反馈位（页面上那一行提示），刷新后再打开页面不会重复计入。
    expect(dir.feedback.value).toContain('手动执行')
    stop()
  })

  it('已在执行的任务不重复触发', async () => {
    const busy = job({ active_run: run({ status: 'running' }) })
    api.listScheduledJobs.mockResolvedValue([busy])
    const { dir, submit, stop } = build()
    await flushPromises()

    await dir.runNow(busy)

    expect(submit).not.toHaveBeenCalled()
    stop()
  })

  it('触发受理后进入待跟踪并展开执行历史', async () => {
    const target = job()
    const { dir, submit, stop } = build()
    await flushPromises()
    submit.mockResolvedValue({ run_id: 'run-9', status: 'queued', details_expired: false })

    await dir.runNow(target)

    expect(dir.awaitedRunIds.get('run-9')).toBe('job-1')
    expect(dir.expanded.value).toEqual({ jobId: 'job-1', kind: 'history' })
    expect(dir.feedback.value).toContain('已受理')
    stop()
  })

  it('触发被拒绝（4xx）时给行内错误，不进待跟踪', async () => {
    const target = job()
    const { dir, submit, stop } = build()
    await flushPromises()
    submit.mockRejectedValue(
      new ApiError({ message: 'forbidden', status: 403, code: 'permission_denied' }),
    )

    await dir.runNow(target)

    expect(dir.awaitedRunIds.size).toBe(0)
    expect(dir.rowErrors.value['job-1']).toContain('没有管理权限')
    // 拒绝也要把历史打开：用户得能看到到底是哪一次被拒了。
    expect(dir.expanded.value).toEqual({ jobId: 'job-1', kind: 'history' })
    stop()
  })

  it('受理情况不明（超时/5xx）时只说待核实，不排队也不重发', async () => {
    const target = job()
    const { dir, submit, stop } = build()
    await flushPromises()
    submit.mockRejectedValue(
      new ApiError({ message: 'gateway timeout', status: 504, code: 'upstream_timeout' }),
    )

    await dir.runNow(target)

    expect(dir.awaitedRunIds.size).toBe(0)
    expect(dir.rowErrors.value['job-1']).toContain('不会自动重复提交')
    stop()
  })
})
