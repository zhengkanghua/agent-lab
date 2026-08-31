import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchComposer from '../components/SearchComposer.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

type ComposerProps = InstanceType<typeof SearchComposer>['$props']

function mountComposer(
  mode: 'document' | 'chunk' = 'document',
  overrides: Partial<ComposerProps> = {},
) {
  return mount(SearchComposer, {
    props: {
      mode,
      modelValue: '央行利率',
      documentLimit: 10,
      topK: 10,
      matchesPerDocument: 3,
      loading: false,
      inputError: null,
      remainingCharacters: 4088,
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

  it('offers one article as the minimum document result count', () => {
    const wrapper = mountComposer()
    const select = wrapper.get<HTMLSelectElement>('select[aria-label="最多显示的新闻数量"]')

    expect([...select.element.options].map((option) => Number(option.value))).toEqual([
      1, 5, 10, 20,
    ])
    expect(wrapper.text()).toContain('每篇相关片段')
  })

  it('switches to the raw Chunk controls without showing per-document options', async () => {
    const wrapper = mountComposer()
    const chunkModeButton = wrapper
      .findAll<HTMLButtonElement>('.mode-switch button')
      .find((button) => button.text().includes('按片段'))

    await chunkModeButton?.trigger('click')
    expect(wrapper.emitted('update:mode')?.[0]).toEqual(['chunk'])

    await wrapper.setProps({ mode: 'chunk' })
    const select = wrapper.get<HTMLSelectElement>('select[aria-label="最多显示的原始片段数量"]')
    expect([...select.element.options].map((option) => Number(option.value))).toEqual([
      1, 5, 10, 20,
    ])
    expect(wrapper.find('.advanced-options').exists()).toBe(false)
    expect(wrapper.text()).toContain('搜索片段')
  })

  /* 以下三条守的是接入 shared/ui 之后的接线。它们原来是手写的，
     漏了不报错、界面也照常，只有读屏用户会听不到。 */

  it('常态下字数说明被输入框的 aria-describedby 指到', () => {
    const wrapper = mountComposer()
    const hint = wrapper.get('.character-count')

    expect(hint.text()).toContain('4,088')
    expect(wrapper.get('.query-input').attributes('aria-describedby')).toBe(
      wrapper.get('.field-hint').attributes('id'),
    )
  })

  it('有输入错误时标 aria-invalid，指向改成错误文字', () => {
    const wrapper = mountComposer('document', { inputError: '请输入检索内容' })
    const textarea = wrapper.get('.query-input')
    const error = wrapper.get('.field-error')

    expect(error.text()).toBe('请输入检索内容')
    expect(error.attributes('role')).toBe('alert')
    expect(textarea.attributes('aria-invalid')).toBe('true')
    expect(textarea.attributes('aria-describedby')).toBe(error.attributes('id'))
    // 错误态下说明位让给错误，字数不再同时被指向。
    expect(wrapper.find('.field-hint').exists()).toBe(false)
  })

  it('搜索中禁用提交并标 aria-busy', () => {
    const wrapper = mountComposer('document', { loading: true })
    const submit = wrapper.get<HTMLButtonElement>('button[type="submit"]')

    expect(submit.element.disabled).toBe(true)
    expect(submit.attributes('aria-busy')).toBe('true')
    expect(wrapper.text()).toContain('正在搜索')
  })
})
