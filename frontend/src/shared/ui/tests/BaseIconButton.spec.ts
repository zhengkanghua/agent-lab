import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'

describe('BaseIconButton', () => {
  it('label 同时落到 aria-label 与 title', () => {
    const wrapper = mount(BaseIconButton, {
      props: { label: '重置密码' },
      slots: { default: '<svg class="my-icon" />' },
    })
    // 没有可见文案的按钮，读屏只会念「按钮」，所以 aria-label 是必填 prop。
    expect(wrapper.attributes('aria-label')).toBe('重置密码')
    expect(wrapper.attributes('title')).toBe('重置密码')
    expect(wrapper.find('.my-icon').exists()).toBe(true)
  })

  it('loading 时禁用、标 aria-busy、图标换成转圈', () => {
    const wrapper = mount(BaseIconButton, {
      props: { label: '刷新', loading: true },
      slots: { default: '<svg class="my-icon" />' },
    })
    expect(wrapper.attributes('disabled')).toBeDefined()
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.find('.base-spinner').exists()).toBe(true)
    expect(wrapper.find('.my-icon').exists()).toBe(false)
  })

  it('busyCursor 落 class，用来区分「等一下」与「不可用」', () => {
    const wrapper = mount(BaseIconButton, { props: { label: '保存', busyCursor: true } })
    expect(wrapper.classes()).toContain('is-busy-cursor')
  })

  it('size 落 class', () => {
    expect(mount(BaseIconButton, { props: { label: 'x' } }).classes()).toContain('is-md')
    expect(mount(BaseIconButton, { props: { label: 'x', size: 'sm' } }).classes()).toContain(
      'is-sm',
    )
  })

  it('禁用时不触发 click', async () => {
    let clicks = 0
    const wrapper = mount(BaseIconButton, {
      props: { label: 'x', disabled: true, onClick: () => (clicks += 1) },
    })
    await wrapper.trigger('click')
    expect(clicks).toBe(0)
  })
})
