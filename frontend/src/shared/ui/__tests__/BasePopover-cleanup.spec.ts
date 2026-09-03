import { mount } from '@vue/test-utils'
import { describe, expect, it, afterEach } from 'vitest'
import { h } from 'vue'
import BasePopover from '../BasePopover.vue'

describe('BasePopover 测试隔离', () => {
  let wrapper: ReturnType<typeof mount> | null = null

  afterEach(() => {
    // 清理所有测试中残留的 popover 内容
    if (wrapper) {
      wrapper.unmount()
      wrapper = null
    }
    document.querySelectorAll('[data-radix-popper-content-wrapper]').forEach((el) => el.remove())
    document.querySelectorAll('.prompt-panel').forEach((el) => el.remove())
  })

  it('每个测试开始前 document 应该是干净的', () => {
    expect(document.querySelector('.prompt-panel')).toBeNull()
  })

  it('测试 A 创建浮层', async () => {
    wrapper = mount(BasePopover, {
      slots: {
        trigger: h('button', { class: 'trigger-a' }, 'A'),
        default: h('div', { class: 'content-a' }, 'Content A'),
      },
      attachTo: document.body,
    })

    await wrapper.find('.trigger-a').trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.content-a')).not.toBeNull()
  })

  it('测试 B 应该看到干净的 document', () => {
    expect(document.querySelector('.content-a')).toBeNull()
    expect(document.querySelector('.prompt-panel')).toBeNull()
  })
})
