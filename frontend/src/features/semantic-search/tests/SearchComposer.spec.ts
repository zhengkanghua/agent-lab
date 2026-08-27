import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchComposer from '../components/SearchComposer.vue'

function mountComposer(mode: 'document' | 'chunk' = 'document') {
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
    },
  })
}

describe('SearchComposer', () => {
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
})
