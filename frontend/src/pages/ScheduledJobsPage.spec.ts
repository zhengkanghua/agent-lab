import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'

const api = vi.hoisted(() => ({
  listScheduledJobs: vi.fn(),
  createScheduledJob: vi.fn(),
  updateScheduledJob: vi.fn(),
  deleteScheduledJob: vi.fn(),
  triggerScheduledJob: vi.fn(),
  listScheduledJobRuns: vi.fn(),
  validateCron: vi.fn(),
}))

/* 部分替换：类型常量（SCHEDULED_JOB_TASK_TYPES 等）用真模块，只有 7 个网络函数换成替身。
   整体替换会让 JobForm 取常量时炸出 unhandled rejection，表单永远打不开提交闸门。 */
vi.mock('../api/scheduled-jobs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/scheduled-jobs')>()
  return {
    ...actual,
    listScheduledJobs: api.listScheduledJobs,
    createScheduledJob: api.createScheduledJob,
    updateScheduledJob: api.updateScheduledJob,
    deleteScheduledJob: api.deleteScheduledJob,
    triggerScheduledJob: api.triggerScheduledJob,
    listScheduledJobRuns: api.listScheduledJobRuns,
    validateCron: api.validateCron,
  }
})

const session = vi.hoisted(() => ({
  initialize: vi.fn(),
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: {
    status: ref('authenticated'),
    user: ref({
      id: '10000000-0000-4000-8000-000000000001',
      email: 'admin@example.com',
      is_active: true,
      is_superuser: true,
      is_verified: true,
      is_environment_admin: true,
    }),
    initialize: session.initialize,
  },
}))

import ScheduledJobsPage from './ScheduledJobsPage.vue'

const syncJob = {
  id: '40000000-0000-4000-8000-000000000001',
  key: 'freshrss-sync',
  task_type: 'freshrss_sync',
  cron_expr: '*/10 * * * *',
  params: { limit_per_source: 2 },
  enabled: true,
  next_run_at: '2026-09-03T01:00:00Z',
  last_run: {
    id: '50000000-0000-4000-8000-000000000001',
    job_id: '40000000-0000-4000-8000-000000000001',
    trigger_type: 'scheduled',
    status: 'succeeded',
    started_at: '2026-09-02T04:00:00Z',
    finished_at: '2026-09-02T04:01:00Z',
    stats: { synchronized_document_count: 3, failures: {} },
    error_type: null,
  },
  created_at: '2026-09-02T00:00:00Z',
  updated_at: '2026-09-02T00:00:00Z',
}

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      {
        path: '/admin/scheduled-jobs',
        name: 'scheduled-jobs',
        component: ScheduledJobsPage,
      },
    ],
  })
}

async function mountPage() {
  const router = testRouter()
  await router.push('/admin/scheduled-jobs')
  await router.isReady()

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  const wrapper = mount(ScheduledJobsPage, {
    attachTo: document.body,
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  api.listScheduledJobs.mockResolvedValue([syncJob])
  api.createScheduledJob.mockReset()
  api.updateScheduledJob.mockReset()
  api.deleteScheduledJob.mockReset()
  api.triggerScheduledJob.mockReset()
  api.listScheduledJobRuns.mockResolvedValue([])
  api.validateCron.mockReset()
  api.validateCron.mockResolvedValue({
    next_run_times: ['2026-09-03T01:00:00Z'],
    next_run_times_local: ['2026-09-03T09:00:00+08:00'],
  })
  session.initialize.mockReset()
})

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

describe('ScheduledJobsPage', () => {
  it('renders the job list with Beijing-time display copy', async () => {
    const wrapper = await mountPage()

    const text = wrapper.text()
    expect(text).toContain('freshrss-sync')
    expect(text).toContain('FreshRSS 同步')
    expect(text).toContain('*/10 * * * *')
    // UTC 01:00 → 北京 09:00（Q6）。
    expect(text).toContain('2026-09-03 09:00:00')
    // 上次执行摘要走中文文案（Q5）。
    expect(text).toContain('成功')
  })

  it('toggling the enable switch sends a PATCH and refreshes', async () => {
    api.updateScheduledJob.mockResolvedValue({ ...syncJob, enabled: false })
    const wrapper = await mountPage()

    await wrapper.get('input[aria-label="启用 freshrss-sync"]').setValue(false)
    await flushPromises()

    expect(api.updateScheduledJob).toHaveBeenCalledWith({
      jobId: syncJob.id,
      enabled: false,
    })
    expect(wrapper.text()).toContain('已停用')
  })

  it('run-now opens the history panel and tracks the awaited run to its terminal state', async () => {
    api.triggerScheduledJob.mockResolvedValue({
      job_id: syncJob.id,
      run_id: '60000000-0000-4000-8000-000000000009',
      status: 'running',
    })
    api.listScheduledJobRuns.mockResolvedValue([
      {
        id: '60000000-0000-4000-8000-000000000009',
        job_id: syncJob.id,
        trigger_type: 'manual',
        status: 'succeeded',
        started_at: '2026-09-02T04:00:00Z',
        finished_at: '2026-09-02T04:02:00Z',
        stats: { synchronized_document_count: 1, failures: {} },
        error_type: null,
      },
    ])
    const wrapper = await mountPage()

    await wrapper.get('button[aria-label="立即执行 freshrss-sync"]').trigger('click')
    await flushPromises()

    expect(api.triggerScheduledJob).toHaveBeenCalledWith(syncJob.id)
    // 历史面板自动展开（Q3：触发后直接纳客）。
    expect(wrapper.text()).toContain('执行历史')
    expect(wrapper.text()).toContain('手动执行完成：成功')
  })

  it('delete requires a second confirming click', async () => {
    api.deleteScheduledJob.mockResolvedValue(undefined)
    const wrapper = await mountPage()

    await wrapper.get('button[aria-label="删除 freshrss-sync"]').trigger('click')
    await flushPromises()
    // 第一次只出现确认态，不发请求。
    expect(api.deleteScheduledJob).not.toHaveBeenCalled()

    await wrapper.get('button[aria-label="确认删除 freshrss-sync"]').trigger('click')
    await flushPromises()
    expect(api.deleteScheduledJob).toHaveBeenCalledWith(syncJob.id)
  })

  it('opening the editor prefills the job and submits through update', async () => {
    api.updateScheduledJob.mockResolvedValue(syncJob)
    const wrapper = await mountPage()

    await wrapper.get('button[aria-label="编辑 freshrss-sync"]').trigger('click')
    await flushPromises()

    const cronInput = wrapper.get('input[name="job-cron"]').element as HTMLInputElement
    expect(cronInput.value).toBe('*/10 * * * *')

    // cron 预览有 300ms 防抖；等预览通过（提交闸门打开）再提交。
    await vi.waitFor(() => expect(wrapper.text()).toContain('接下来 3 次'))
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: syncJob.id, cronExpr: '*/10 * * * *' }),
    )
  })
})
