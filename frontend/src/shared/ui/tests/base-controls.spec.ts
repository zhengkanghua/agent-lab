import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseCallout from '../BaseCallout.vue'
import BaseInput from '../BaseInput.vue'
import BaseSelect from '../BaseSelect.vue'
import BaseSuggestionList from '../BaseSuggestionList.vue'
import BaseTextarea from '../BaseTextarea.vue'

describe('BaseInput', () => {
  it('输入经 v-model 抛出字符串，attrs（含 BaseField 的 id/aria）透传到 input', async () => {
    const emitted: Array<string | number> = []
    const wrapper = mount(BaseInput, {
      attrs: { id: 'given-id', 'aria-invalid': 'true' },
      props: {
        modelValue: '',
        'onUpdate:modelValue': (value: string | number) => emitted.push(value),
      },
    })

    await wrapper.get('input').setValue('abc')

    expect(emitted).toEqual(['abc'])
    const input = wrapper.get('input')
    expect(input.attributes('id')).toBe('given-id')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  it('type=number 抛出数值，空串保持空串', async () => {
    const emitted: Array<string | number> = []
    const wrapper = mount(BaseInput, {
      props: {
        type: 'number',
        'onUpdate:modelValue': (value: string | number) => emitted.push(value),
      },
    })

    await wrapper.get('input').setValue('42')
    await wrapper.get('input').setValue('')

    expect(emitted).toEqual([42, ''])
  })
})

describe('BaseSelect', () => {
  it('change 抛出选中的字符串，选项由插槽提供', async () => {
    const emitted: string[] = []
    const wrapper = mount(BaseSelect, {
      props: {
        modelValue: '10',
        'onUpdate:modelValue': (value: string) => emitted.push(value),
      },
      slots: {
        default: '<option value="1">1</option><option value="10">10</option>',
      },
      attrs: { id: 'probe' },
    })

    await wrapper.get('select').setValue('1')

    expect(wrapper.get('select').attributes('id')).toBe('probe')
    expect(emitted).toEqual(['1'])
  })
})

describe('BaseTextarea', () => {
  it('多行输入走 v-model，mono 档切换等宽字体', async () => {
    const emitted: string[] = []
    const wrapper = mount(BaseTextarea, {
      props: {
        modelValue: '',
        mono: true,
        'onUpdate:modelValue': (value: string) => emitted.push(value),
      },
    })

    await wrapper.get('textarea').setValue('你是财经记者。')

    expect(emitted).toEqual(['你是财经记者。'])
    expect(wrapper.get('textarea').classes()).toContain('is-mono')
  })
})

describe('BaseCallout', () => {
  it('danger 带 role=alert，标题与说明分开渲染', () => {
    const wrapper = mount(BaseCallout, {
      props: { tone: 'danger', title: '读取失败', description: '请稍后重试。' },
    })

    expect(wrapper.get('.base-callout').attributes('role')).toBe('alert')
    expect(wrapper.get('.callout-title').text()).toBe('读取失败')
    expect(wrapper.get('.callout-description').text()).toBe('请稍后重试。')
  })

  it('actions 插槽渲染在正文之后，neutral 不带 alert 语义', () => {
    const wrapper = mount(BaseCallout, {
      props: { tone: 'neutral', description: '较早的对话已被压缩。' },
      slots: { actions: '<button type="button">重试</button>' },
    })

    expect(wrapper.find('.base-callout').attributes('role')).toBeUndefined()
    expect(wrapper.get('.callout-actions button').text()).toBe('重试')
  })
})

describe('BaseSuggestionList', () => {
  it('每个示例渲染成整行按钮，点击抛出原文', async () => {
    const selected: string[] = []
    const wrapper = mount(BaseSuggestionList, {
      props: {
        examples: ['最近有哪些关于利率的报道？', '新能源行业动态'],
        onSelect: (value: string) => selected.push(value),
      },
    })

    const buttons = wrapper.findAll('.suggestion-button')
    expect(buttons).toHaveLength(2)

    await buttons[1]!.trigger('click')
    expect(selected).toEqual(['新能源行业动态'])
  })
})
