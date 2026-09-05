import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AgentComposer from '../components/AgentComposer.vue'
import { MAX_MESSAGE_CHARACTERS } from '../model/agent-validation'

type ComposerProps = InstanceType<typeof AgentComposer>['$props']

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/settings/:section?', name: 'settings', component: { template: '<div />' } },
    ],
  })
}

function mountComposer(overrides: Partial<ComposerProps> = {}) {
  return mount(AgentComposer, {
    props: {
      modelValue: '央行利率',
      customPromptActive: false,
      inputError: null,
      remainingCharacters: 3996,
      streaming: false,
      canSend: true,
      hasHistory: false,
      ...overrides,
    },
    global: { plugins: [testRouter()] },
  })
}

describe('AgentComposer', () => {
  it('提问输入框的上界与校验常量同源', () => {
    // 否则会出现「能打进去但一提交就报错」。系统提示词的上界在设置中心的编辑器里测。
    const wrapper = mountComposer()

    expect(wrapper.get('.message-input').attributes('maxlength')).toBe(
      String(MAX_MESSAGE_CHARACTERS),
    )
  })

  it('Enter 发送', async () => {
    const wrapper = mountComposer()

    await wrapper.get('.message-input').trigger('keydown.enter')

    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('Shift + Enter 只换行，不发送', async () => {
    const wrapper = mountComposer()

    await wrapper.get('.message-input').trigger('keydown.enter', { shiftKey: true })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('输入法组合期间的 Enter 不发送', async () => {
    const wrapper = mountComposer()

    // 中文输入按 Enter 是「确认候选词」，不拦住会把半个词发出去。
    await wrapper.get('.message-input').trigger('keydown.enter', { isComposing: true })

    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('不能发送时 Enter 不发送', async () => {
    const wrapper = mountComposer({ canSend: false })

    await wrapper.get('.message-input').trigger('keydown.enter')

    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('剩余字数临近与超出上界时换配色', async () => {
    const wrapper = mountComposer({ remainingCharacters: 150 })
    expect(wrapper.get('.character-count').classes()).toContain('is-near')

    await wrapper.setProps({ remainingCharacters: -3 })
    expect(wrapper.get('.character-count').classes()).toContain('is-over')
  })

  it('流式期间把发送换成停止生成', async () => {
    const wrapper = mountComposer({ streaming: true, canSend: false, hasHistory: true })

    expect(wrapper.find('.send-button').exists()).toBe(false)
    await wrapper.get('.stop-button').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)

    // 流式期间不让开新会话：会把正在写的这一轮丢掉。
    expect(wrapper.get('.secondary-button').attributes('disabled')).toBeDefined()
  })

  it('没有历史时不显示新会话按钮', () => {
    const wrapper = mountComposer({ hasHistory: false })
    expect(wrapper.find('.secondary-button').exists()).toBe(false)
  })

  it('有历史时新会话按钮可用并冒泡事件', async () => {
    const wrapper = mountComposer({ hasHistory: true })

    await wrapper.get('.secondary-button').trigger('click')

    expect(wrapper.emitted('new-conversation')).toHaveLength(1)
  })

  it('偏好里存了自定义提示词时亮徽章并链到设置页，默认时不打扰', async () => {
    const wrapper = mountComposer({ customPromptActive: false })
    expect(wrapper.find('.prompt-badge-link').exists()).toBe(false)

    await wrapper.setProps({ customPromptActive: true })

    const badge = wrapper.get('.prompt-badge-link')
    expect(badge.attributes('aria-label')).toContain('自定义提示词')
    expect(badge.attributes('href')).toBe('/settings/agent')
  })

  it('校验错误挂到输入框的 aria-describedby 上', async () => {
    const wrapper = mountComposer({ inputError: '请输入想问 Agent 的问题。' })

    expect(wrapper.get('.field-error').attributes('role')).toBe('alert')
    expect(wrapper.get('.message-input').attributes('aria-describedby')).toBe('agent-message-error')
    expect(wrapper.get('.message-input').attributes('aria-invalid')).toBe('true')

    await wrapper.setProps({ inputError: null })
    expect(wrapper.get('.message-input').attributes('aria-describedby')).toBe('agent-message-count')
  })
})
