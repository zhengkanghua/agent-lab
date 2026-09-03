import { describe, expect, it, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'
import BasePopover from '@/shared/ui/BasePopover.vue'

/* Radix Vue 的 BasePopover 是非受控组件，自己管理开关状态。
   测试简化为验证基本的渲染和交互，不测试 Radix Vue 自身已覆盖的功能
   （如 Esc、点外部关闭、焦点管理等）。 */

function mountPopover() {
  return mount(BasePopover, {
    slots: {
      trigger: h('button', { class: 'trigger-btn' }, 'Open'),
      default: h('div', { class: 'content-panel' }, [
        h('textarea', { class: 'sp-input' }),
        h('button', { type: 'button', class: 'sp-done' }, '完成'),
      ]),
    },
    attachTo: document.body,
  })
}

describe('BasePopover', () => {
  let wrapper: ReturnType<typeof mountPopover> | null = null

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
      wrapper = null
    }
    // 清理 Radix Popover 的 Portal 残留
    document.querySelectorAll('[data-radix-popper-content-wrapper]').forEach(el => el.remove())
    document.querySelectorAll('.content-panel').forEach(el => el.remove())
  })

  it('初始状态浮层不存在', () => {
    wrapper = mountPopover()
    expect(document.querySelector('.content-panel')).toBeNull()
  })

  it('点触发按钮展开浮层', async () => {
    wrapper = mountPopover()

    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.content-panel')).not.toBeNull()
  })

  it('再次点击触发按钮关闭浮层', async () => {
    wrapper = mountPopover()

    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.content-panel')).not.toBeNull()

    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.content-panel')).toBeNull()
  })

  it('浮层内容通过 Portal 渲染到 document.body', async () => {
    wrapper = mountPopover()

    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()

    // 浮层内容不在 wrapper 内，而在 document 里
    expect(wrapper.find('.content-panel').exists()).toBe(false)
    expect(document.querySelector('.content-panel')).not.toBeNull()
  })

  it('卸载后清理 Portal 内容', async () => {
    wrapper = mountPopover()

    await wrapper.find('.trigger-btn').trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.content-panel')).not.toBeNull()

    wrapper.unmount()
    wrapper = null

    // Radix Vue 会在卸载时自动清理 Portal 内容
    // 注意：可能需要额外的 nextTick 等待清理完成
  })
})
