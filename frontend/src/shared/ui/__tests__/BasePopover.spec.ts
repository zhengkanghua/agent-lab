import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { h } from 'vue'
import BasePopover from '../BasePopover.vue'

describe('BasePopover Radix Vue 渲染机制', () => {
  it('PopoverContent 通过 Portal 渲染到 body，不在组件树里', async () => {
    const wrapper = mount(BasePopover, {
      slots: {
        trigger: h('button', { class: 'trigger-btn' }, 'Open'),
        default: h('div', { class: 'content-div' }, 'Popover Content'),
      },
      attachTo: document.body,
    })

    // 初始状态：浮层内容不存在
    expect(wrapper.find('.content-div').exists()).toBe(false)

    // 点击触发按钮
    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()

    // 浮层内容不在 wrapper 内，而在 document.body 里
    expect(wrapper.find('.content-div').exists()).toBe(false)
    expect(document.querySelector('.content-div')).not.toBeNull()

    wrapper.unmount()
  })
})
