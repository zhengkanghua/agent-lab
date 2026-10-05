import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import KnowledgeBaseScopePicker from '@/shared/ui/KnowledgeBaseScopePicker.vue'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'

/* 加载态与「不能用」必须分得开。
 *
 * 两者共用 useKnowledgeBaseScope 的那一份 `error`（它同时是检索页与 Agent 页的提交守卫，
 * 所以形状不能改），但表现上不是一回事：加载中不是故障，原来它被画成琥珀色告警、和
 * 「知识库目录加载失败」共用同一条提示，读起来像出事了；同一时刻胶囊还会说
 * 「所有知识库（0）」——那个 0 是「还没读到」，不是「一个都没有」。
 */

const bases = [newsKnowledgeBase, { ...techKnowledgeBase, is_active: true }]

describe('KnowledgeBaseScopePicker', () => {
  it('加载中胶囊说正在加载，不报数量、也不出告警', () => {
    const wrapper = mount(KnowledgeBaseScopePicker, {
      props: { modelValue: { mode: 'all' }, knowledgeBases: [], loading: true },
    })

    expect(wrapper.get('.popover-trigger').text()).toContain('正在加载知识库')
    expect(wrapper.find('.scope-error').exists()).toBe(false)
  })

  it('加载完成后再报数量', () => {
    const wrapper = mount(KnowledgeBaseScopePicker, {
      props: { modelValue: { mode: 'all' }, knowledgeBases: bases },
    })

    expect(wrapper.get('.popover-trigger').text()).toContain('所有知识库（2）')
  })

  /* 这三种是「确实不能用」，仍然要显眼：它们会挡住提交。 */
  it('读失败、没有启用的库、选择为空时都以告警呈现', () => {
    const failed = mount(KnowledgeBaseScopePicker, {
      props: { modelValue: { mode: 'all' }, knowledgeBases: [], error: '知识库目录加载失败。' },
    })
    expect(failed.get('.scope-error').text()).toContain('知识库目录加载失败')

    const emptySelection = mount(KnowledgeBaseScopePicker, {
      props: {
        modelValue: { mode: 'selected', knowledge_base_ids: [] },
        knowledgeBases: bases,
        error: '至少选择一个知识库。',
      },
    })
    expect(emptySelection.get('.scope-error').text()).toContain('至少选择一个知识库')

    const noBases = mount(KnowledgeBaseScopePicker, {
      props: { modelValue: { mode: 'all' }, knowledgeBases: [], error: '没有启用的知识库。' },
    })
    expect(noBases.get('.scope-error').text()).toContain('没有启用的知识库')
  })
})
