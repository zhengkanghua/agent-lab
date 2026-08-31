import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'

describe('BaseSpinner', () => {
  it('无 label 时对读屏隐藏', () => {
    const wrapper = mount(BaseSpinner)
    // 外层通常已有 aria-busy 或状态文案，再念一遍「加载中」是噪音。
    expect(wrapper.attributes('aria-hidden')).toBe('true')
    expect(wrapper.attributes('role')).toBeUndefined()
    expect(wrapper.attributes('aria-label')).toBeUndefined()
  })

  it('给了 label 就转为 status，不再隐藏', () => {
    const wrapper = mount(BaseSpinner, { props: { label: '正在生成' } })
    expect(wrapper.attributes('aria-hidden')).toBeUndefined()
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-label')).toBe('正在生成')
  })

  it('size 透传给图标', () => {
    const wrapper = mount(BaseSpinner, { props: { size: 24 } })
    expect(wrapper.attributes('width')).toBe('24')
    expect(wrapper.attributes('height')).toBe('24')
  })
})
