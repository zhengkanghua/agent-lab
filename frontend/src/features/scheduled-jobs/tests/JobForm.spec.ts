import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { newsKnowledgeBase } from '@/api/knowledge-bases.fixture'
import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'

/* 只打桩 cron 预览那一个接口：本文件要验的是提交键的可用性，
   而它取决于「预览已通过」——不打桩的话按钮会一直因为预览未就绪而禁用，
   那条断言就证明不了任何事。 */
const api = vi.hoisted(() => ({ validateCron: vi.fn() }))
vi.mock('@/api/scheduled-jobs', () => api)

import JobForm from '../components/JobForm.vue'

/* 清理范围里有「找不到的库」时，提交键必须置灰。
 *
 * 这条原来是个死角：onSubmit 里直接 return，按钮却是可点的——用户点一下毫无反应，
 * 只会以为界面坏了。原因（「部分知识库未找到」）虽然写在字段旁边，但拦在提交那一步
 * 而不置灰，等于把「能不能提交」这件事留给用户去猜。
 */

const MISSING_ID = '30000000-0000-4000-8000-0000000000ff'

/** prune 的类型元数据：三个字段都在，提交键才有机会可用。 */
const pruneSpec: ScheduledTaskTypeDto = {
  task_type: 'prune_old_documents',
  label: '清理旧文档',
  description: '',
  defaults: { retention_days: 30, dry_run: true, knowledge_base_ids: [] },
  params_schema: {
    type: 'object',
    properties: {
      retention_days: { type: 'integer', minimum: 1, maximum: 3650 },
      dry_run: { type: 'boolean' },
      knowledge_base_ids: { type: 'array' },
    },
  },
} as unknown as ScheduledTaskTypeDto

function mountForm(ids: string[]) {
  return mount(JobForm, {
    props: {
      mode: 'create',
      job: null,
      keyValue: 'prune-old',
      taskType: 'prune_old_documents',
      cronExpr: '*/10 * * * *',
      limitPerSource: 2,
      batchSize: 10,
      staleAfterMinutes: 60,
      retentionDays: 30,
      dryRun: true,
      knowledgeBaseIds: ids,
      knowledgeBaseOptions: [newsKnowledgeBase],
      taskTypes: [pruneSpec],
      enabled: true,
      errors: {},
      formError: '',
      submitting: false,
    },
    global: { stubs: { RouterLink: true } },
  })
}

/** 把 cron 预览推到「已通过」——按钮的另一个禁用条件。 */
async function settlePreview() {
  api.validateCron.mockResolvedValue({
    next_run_times: ['2026-10-06T01:00:00Z'],
    next_run_times_local: ['2026-10-06T09:00:00+08:00'],
  })
  await vi.advanceTimersByTimeAsync(300)
  await flushPromises()
}

function submitButton(wrapper: ReturnType<typeof mountForm>) {
  return wrapper.get('button[type="submit"]')
}

describe('JobForm 清理范围的提交闸门', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    api.validateCron.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('范围里有找不到的库时提交键置灰，并把原因摆在旁边', async () => {
    const wrapper = mountForm([newsKnowledgeBase.id, MISSING_ID])
    await settlePreview()

    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('部分知识库未找到')
  })

  it('范围全部能对上时提交键可用', async () => {
    const wrapper = mountForm([newsKnowledgeBase.id])
    await settlePreview()

    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
  })
})
