import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchComposer from '../components/SearchComposer.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

type ComposerProps = InstanceType<typeof SearchComposer>['$props']

function mountComposer(overrides: Partial<ComposerProps> = {}) {
  return mount(SearchComposer, {
    props: {
      modelValue: '央行利率',
      documentLimit: 10,
      matchesPerDocument: 3,
      loading: false,
      inputError: null,
      remainingCharacters: 4088,
      hasRecords: false,
      ...overrides,
    },
  })
}

describe('SearchComposer', () => {
  // 输入框上界必须与校验用的常量同源，否则会出现「能打进去但一提交就报错」。
  it('caps the textarea at the shared query length limit', () => {
    const textarea = mountComposer().get<HTMLTextAreaElement>('.query-input')

    expect(textarea.attributes('maxlength')).toBe(String(MAX_QUERY_CHARACTERS))
  })

  it('offers article count options from one to twenty', () => {
    const wrapper = mountComposer()
    const select = wrapper.get<HTMLSelectElement>('select[aria-label*="新闻数量"]')

    expect([...select.element.options].map((option) => Number(option.value))).toEqual([
      1, 5, 10, 20,
    ])
  })

  it('has no mode switch (chunk mode removed)', () => {
    const wrapper = mountComposer()

    expect(wrapper.find('.mode-switch').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('按片段')
    expect(wrapper.text()).toContain('搜索新闻')
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
    expect(wrapper.text()).toContain('正在搜索')
  })

  it('input error is announced and marked aria-invalid', () => {
    const wrapper = mountComposer({ inputError: '请输入检索内容' })
    const textarea = wrapper.get('.query-input')

    expect(textarea.attributes('aria-invalid')).toBe('true')
    expect(wrapper.text()).toContain('请输入检索内容')
  })
})
