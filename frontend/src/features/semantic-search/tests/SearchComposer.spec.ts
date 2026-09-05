import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import SearchComposer from '../components/SearchComposer.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

type ComposerProps = InstanceType<typeof SearchComposer>['$props']

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
  return mount(SearchComposer, {
    props: {
      modelValue: '央行利率',
      loading: false,
      inputError: null,
      remainingCharacters: 4088,
      hasRecords: false,
      preferenceSummary: '每次检索 10 篇 · 每篇 3 条（在设置中调整）',
      ...overrides,
    },
    global: { plugins: [testRouter()] },
  })
}

describe('SearchComposer', () => {
  // 输入框上界必须与校验用的常量同源，否则会出现「能打进去但一提交就报错」。
  it('caps the textarea at the shared query length limit', () => {
    const textarea = mountComposer().get<HTMLTextAreaElement>('.query-input')

    expect(textarea.attributes('maxlength')).toBe(String(MAX_QUERY_CHARACTERS))
  })

  it('数量参数不在输入条里：它们是设置中心的检索偏好', () => {
    const wrapper = mountComposer()

    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('新闻数量')
    // 偏好入口保留：图标链接直达设置页，当前值放在 title 里。
    const prefsLink = wrapper.get('.prefs-link')
    expect(prefsLink.attributes('aria-label')).toBe('检索偏好设置')
    expect(prefsLink.attributes('title')).toContain('每次检索 10 篇')
  })

  it('has no mode switch (chunk mode removed)', () => {
    const wrapper = mountComposer()

    expect(wrapper.find('.mode-switch').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('按片段')
    // Submit button no longer has text, so we check aria-label instead
    expect(wrapper.get('.search-submit').attributes('aria-label')).toBe('搜索新闻')
  })

  it('shows the clear-stream button only after there are records', async () => {
    const wrapper = mountComposer({ hasRecords: false })
    expect(wrapper.find('.clear-button').exists()).toBe(false)

    await wrapper.setProps({ hasRecords: true })
    expect(wrapper.find('.clear-button').exists()).toBe(true)
  })

  it('emits submit on form submit and clear on the clear button', async () => {
    const wrapper = mountComposer({ hasRecords: true })

    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('submit')).toHaveLength(1)

    await wrapper.get('.clear-button').trigger('click')
    expect(wrapper.emitted('clear')).toHaveLength(1)
  })

  it('Enter sends while composing is ignored and shift+enter does not send', async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.get<HTMLTextAreaElement>('.query-input')

    await textarea.trigger('keydown.enter')
    expect(wrapper.emitted('submit')).toHaveLength(1)

    // 输入法组合期间 Enter 不提交。
    const imeEvent = { key: 'Enter', isComposing: true, shiftKey: false } as KeyboardEvent
    await textarea.trigger('keydown', imeEvent)
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('loading disables submit and marks aria-busy', () => {
    const wrapper = mountComposer({ loading: true })
    const submit = wrapper.get<HTMLButtonElement>('button[type="submit"]')

    expect(submit.element.disabled).toBe(true)
    expect(wrapper.get('form').attributes('aria-busy')).toBe('true')
  })

  it('input error is announced and marked aria-invalid', () => {
    const wrapper = mountComposer({ inputError: '请输入检索内容' })
    const textarea = wrapper.get('.query-input')

    expect(textarea.attributes('aria-invalid')).toBe('true')
    expect(wrapper.text()).toContain('请输入检索内容')
  })
})
