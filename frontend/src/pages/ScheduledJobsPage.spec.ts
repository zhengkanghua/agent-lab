import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { taskTypes } from '@/api/scheduled-jobs.fixture'
import { ApiError } from '@/api/client'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'

enableAutoUnmount(afterEach)

const api = vi.hoisted(() => ({
  listScheduledJobs: vi.fn(),
  listScheduledTaskTypes: vi.fn(),
  getScheduledJobRun: vi.fn(),
  createScheduledJob: vi.fn(),
  updateScheduledJob: vi.fn(),
  deleteScheduledJob: vi.fn(),
  triggerScheduledJob: vi.fn(),
  listScheduledJobRuns: vi.fn(),
  validateCron: vi.fn(),
  listKnowledgeBases: vi.fn(),
}))

/* 部分替换：类型常量（SCHEDULED_JOB_TASK_TYPES 等）用真模块，只有 7 个网络函数换成替身。
   整体替换会让 JobForm 取常量时炸出 unhandled rejection，表单永远打不开提交闸门。 */
vi.mock('../api/scheduled-jobs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/scheduled-jobs')>()
  return {
    ...actual,
    listScheduledJobs: api.listScheduledJobs,
    listScheduledTaskTypes: api.listScheduledTaskTypes,
    getScheduledJobRun: api.getScheduledJobRun,
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

vi.mock('@/api/knowledge-bases', () => ({
  listKnowledgeBases: api.listKnowledgeBases,
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
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.clearAllMocks()
  sessionStorage.clear()
  api.listScheduledTaskTypes.mockResolvedValue(taskTypes)
  api.listKnowledgeBases.mockResolvedValue([
    {
      id: '10000000-0000-4000-8000-000000000010',
      key: 'news',
      name: '新闻',
      description: null,
      is_active: true,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
    },
    {
      id: '10000000-0000-4000-8000-000000000011',
      key: 'tech-notes',
      name: '技术资料',
      description: null,
      is_active: true,
      created_at: '2026-09-06T00:00:00Z',
      updated_at: '2026-09-06T00:00:00Z',
    },
  ])
  api.getScheduledJobRun.mockImplementation(async (jobId: string, runId: string) => ({
    ...syncJob.last_run,
    id: runId,
    job_id: jobId,
    status: 'running',
    finished_at: null,
    needs_attention: false,
  }))
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
    timezone: 'Asia/Shanghai',
  })
  session.initialize.mockReset()
})

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('ScheduledJobsPage', () => {
  it('shows every saved scope and can retain only an inactive knowledge base', async () => {
    api.listKnowledgeBases.mockResolvedValue([newsKnowledgeBase, techKnowledgeBase])
    api.listScheduledJobs.mockResolvedValue([
      {
        ...syncJob,
        key: 'retention-inactive',
        task_type: 'prune_old_documents',
        enabled: false,
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: [newsKnowledgeBase.id, techKnowledgeBase.id],
        },
      },
    ])
    const wrapper = await mountPage()
    expect(api.listKnowledgeBases).toHaveBeenCalledWith(true)
    await wrapper.get('button[aria-label="编辑 retention-inactive"]').trigger('click')
    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.text()).toContain('接下来 3 次')
    const news = wrapper.get<HTMLInputElement>('input[aria-label="清理知识库 新闻"]')
    const tech = wrapper.get<HTMLInputElement>('input[aria-label="清理知识库 技术资料"]')
    expect(news.element.checked).toBe(true)
    expect(tech.element.checked).toBe(true)
    expect(wrapper.text()).toContain('技术资料（已停用）')
    await news.setValue(false)
    await tech.setValue(false)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请至少选择一个知识库')
    await tech.setValue(true)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: [techKnowledgeBase.id],
        },
      }),
    )
  })

  it('makes an unresolved saved scope visible and removable before saving', async () => {
    const unknownId = '10000000-0000-4000-8000-000000000099'
    api.listScheduledJobs.mockResolvedValue([
      {
        ...syncJob,
        key: 'retention-unknown',
        task_type: 'prune_old_documents',
        enabled: false,
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: [newsKnowledgeBase.id, unknownId],
        },
      },
    ])
    const wrapper = await mountPage()
    await wrapper.get('button[aria-label="编辑 retention-unknown"]').trigger('click')
    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.text()).toContain('接下来 3 次')
    const unknown = wrapper.get<HTMLInputElement>(`input[value="${unknownId}"]`)
    expect(unknown.element.checked).toBe(true)
    expect(wrapper.text()).toContain(`未找到的知识库 ${unknownId}`)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).not.toHaveBeenCalled()
    await unknown.setValue(false)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: [newsKnowledgeBase.id],
        },
      }),
    )
  })

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
    expect(wrapper.text()).toContain('手动执行：成功')
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
    api.listScheduledJobs.mockResolvedValue([{ ...syncJob, enabled: false }])
    api.updateScheduledJob.mockResolvedValue(syncJob)
    const wrapper = await mountPage()

    await wrapper.get('button[aria-label="编辑 freshrss-sync"]').trigger('click')
    await flushPromises()

    const cronInput = wrapper.get('input[name="job-cron"]').element as HTMLInputElement
    expect(cronInput.value).toBe('*/10 * * * *')

    // 推进防抖计时，让预览完成后再提交。
    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.text()).toContain('接下来 3 次')
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: syncJob.id, cronExpr: '*/10 * * * *', enabled: false }),
    )
  })

  it('blocks editing enabled jobs and deleting active executions', async () => {
    api.listScheduledJobs.mockResolvedValue([
      { ...syncJob, active_run: { ...syncJob.last_run, status: 'running' } },
    ])
    const wrapper = await mountPage()
    expect(
      wrapper.get('button[aria-label="编辑 freshrss-sync"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.get('button[aria-label="删除 freshrss-sync"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.get('input[aria-label="启用 freshrss-sync"]').attributes('disabled'),
    ).toBeUndefined()
  })

  it('edits the existing retention type with backend defaults and a boolean dry run', async () => {
    api.listScheduledJobs.mockResolvedValue([
      {
        ...syncJob,
        key: 'retention',
        task_type: 'prune_old_documents',
        enabled: false,
        params: { retention_days: 180, dry_run: true },
      },
    ])
    const wrapper = await mountPage()
    await wrapper.get('button[aria-label="编辑 retention"]').trigger('click')
    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.text()).toContain('接下来 3 次')
    expect((wrapper.get('input[name="retention-days"]').element as HTMLInputElement).value).toBe(
      '180',
    )
    // 旧配置没有范围字段：回填为空，校验强制要求至少补选一个库。
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请至少选择一个知识库')
    await wrapper.get('input[aria-label="清理知识库 新闻"]').setValue(true)
    await wrapper.get('input[name="dry-run"]').setValue(false)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({
        params: {
          retention_days: 180,
          dry_run: false,
          knowledge_base_ids: ['10000000-0000-4000-8000-000000000010'],
        },
        enabled: false,
      }),
    )
  })

  it('renders scope checkboxes from stored knowledge_base_ids on edit', async () => {
    api.listScheduledJobs.mockResolvedValue([
      {
        ...syncJob,
        key: 'retention-multi',
        task_type: 'prune_old_documents',
        enabled: false,
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: [
            '10000000-0000-4000-8000-000000000010',
            '10000000-0000-4000-8000-000000000011',
          ],
        },
      },
    ])
    const wrapper = await mountPage()
    await wrapper.get('button[aria-label="编辑 retention-multi"]').trigger('click')
    await vi.advanceTimersByTimeAsync(300)
    expect(wrapper.text()).toContain('接下来 3 次')
    expect(wrapper.findAll('input[aria-label^="清理知识库"]').length).toBe(2)
    // 取消勾选其中一个后提交，范围只保留剩下的库。
    await wrapper.get('input[aria-label="清理知识库 技术资料"]').setValue(false)
    await wrapper.get('form.job-form').trigger('submit')
    await flushPromises()
    expect(api.updateScheduledJob).toHaveBeenCalledWith(
      expect.objectContaining({
        params: {
          retention_days: 90,
          dry_run: true,
          knowledge_base_ids: ['10000000-0000-4000-8000-000000000010'],
        },
      }),
    )
  })

  it('keeps an unknown task visible without offering an unsupported editor', async () => {
    api.listScheduledJobs.mockResolvedValue([
      { ...syncJob, task_type: 'future_task', enabled: false },
      { ...syncJob, id: 'other-job', key: 'known-job' },
    ])
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('future_task')
    expect(wrapper.text()).toContain('known-job')
    expect(
      wrapper.get('button[aria-label="编辑 freshrss-sync"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('reports metadata loading failures and disables creation', async () => {
    api.listScheduledTaskTypes.mockRejectedValue(new Error('offline'))
    const wrapper = await mountPage()
    expect(wrapper.get('[role="alert"]').text()).toContain('读取任务类型失败')
    const create = wrapper.findAll('button').find((button) => button.text() === '新建任务')!
    expect(create.attributes('disabled')).toBeDefined()
  })

  it('ignores repeated clicks and follows the receipt after leaving the page', async () => {
    let accept!: (receipt: unknown) => void
    api.triggerScheduledJob.mockImplementation(
      () =>
        new Promise((resolve) => {
          accept = resolve
        }),
    )
    const wrapper = await mountPage()
    const button = wrapper.get('button[aria-label="立即执行 freshrss-sync"]')
    await button.trigger('click')
    await button.trigger('click')
    expect(api.triggerScheduledJob).toHaveBeenCalledTimes(1)
    const runId = '60000000-0000-4000-8000-000000000010'
    accept({ job_id: syncJob.id, run_id: runId, status: 'running' })
    await flushPromises()
    await wrapper.get('button[aria-label="查看 freshrss-sync 的执行历史"]').trigger('click')
    wrapper.unmount()
    // 历史列表没有目标记录，重新打开仍用回执中的准确 ID 查询。
    api.getScheduledJobRun.mockResolvedValue({
      ...syncJob.last_run,
      id: runId,
      status: 'succeeded',
    })
    const reopened = await mountPage()
    await flushPromises()
    expect(api.getScheduledJobRun).toHaveBeenCalledWith(syncJob.id, runId, expect.any(AbortSignal))
    expect(reopened.text()).toContain('手动执行：成功')
    expect(api.triggerScheduledJob).toHaveBeenCalledTimes(1)
  })

  it('reports an uncertain receipt on timeout without resubmitting', async () => {
    api.triggerScheduledJob.mockRejectedValue(
      new ApiError({ code: 'request_timeout', message: 'timeout' }),
    )
    const wrapper = await mountPage()
    await wrapper.get('button[aria-label="立即执行 freshrss-sync"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('受理情况待核实')
    expect(api.triggerScheduledJob).toHaveBeenCalledTimes(1)
    expect(api.listScheduledJobRuns).toHaveBeenCalled()
  })
})
