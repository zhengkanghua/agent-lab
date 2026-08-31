import { describe, expect, it } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import BasePopover from '@/shared/ui/BasePopover.vue'

/* 浮层是受控的，焦点归还又发生在浮层被移除之后，所以测试必须有个真的
   父组件来翻 open。直接 setProps 模拟不出「关闭 → 焦点回到触发元素」这一串。
   同时 attachTo document.body：不进真实文档树，jsdom 里 focus() 不生效。 */
const Host = defineComponent({
  props: { initialOpen: { type: Boolean, default: false } },
  setup(props) {
    const open = ref(props.initialOpen)
    return () =>
      h(
        BasePopover,
        {
          open: open.value,
          label: '提示词设置',
          'onUpdate:open': (next: boolean) => {
            open.value = next
          },
        },
        {
          trigger: ({ toggle, attrs }: { toggle: () => void; attrs: Record<string, unknown> }) =>
            h('button', { type: 'button', onClick: toggle, ...attrs }, '设置'),
          default: ({ close }: { close: () => void }) => [
            h('textarea', { class: 'sp-input' }),
            h('button', { type: 'button', class: 'sp-done', onClick: close }, '完成'),
          ],
        },
      )
  },
})

function mountHost(initialOpen = false) {
  return mount(Host, { props: { initialOpen }, attachTo: document.body })
}

function pointerDownOn(target: Element | Document): void {
  target.dispatchEvent(new Event('pointerdown', { bubbles: true }))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

describe('BasePopover', () => {
  it('关闭时不渲染浮层', () => {
    const wrapper = mountHost()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('点触发元素展开，再点收起', async () => {
    const wrapper = mountHost()

    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)

    await wrapper.find('button').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('浮层带上 label 作为可访问名', async () => {
    const wrapper = mountHost(true)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').attributes('aria-label')).toBe('提示词设置')
    wrapper.unmount()
  })

  /* aria-controls 指向的元素必须真的存在，所以关闭时不能留着这个属性。 */
  it('触发元素的 aria 状态跟随开合', async () => {
    const wrapper = mountHost()
    const trigger = wrapper.find('button')

    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(trigger.attributes('aria-controls')).toBeUndefined()

    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(trigger.attributes('aria-controls')).toBe(
      wrapper.find('[role="dialog"]').attributes('id'),
    )
    wrapper.unmount()
  })

  it('展开后焦点落到浮层里第一个可聚焦元素', async () => {
    const wrapper = mountHost()

    await wrapper.find('button').trigger('click')
    await wrapper.vm.$nextTick()

    expect(document.activeElement).toBe(wrapper.find('.sp-input').element)
    wrapper.unmount()
  })

  it('Esc 关闭浮层', async () => {
    const wrapper = mountHost(true)
    await wrapper.vm.$nextTick()

    pressEscape()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('Esc 关闭后焦点回到触发元素', async () => {
    const wrapper = mountHost()
    const trigger = wrapper.find('button')

    await trigger.trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.activeElement).not.toBe(trigger.element)

    pressEscape()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('点浮层外部关闭', async () => {
    const wrapper = mountHost(true)
    await wrapper.vm.$nextTick()

    pointerDownOn(document.body)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  /* 点外部时用户的注意力已经在别处，把焦点抢回触发元素是打扰。 */
  it('点外部关闭不抢回焦点', async () => {
    const wrapper = mountHost()
    const trigger = wrapper.find('button')
    await trigger.trigger('click')
    await wrapper.vm.$nextTick()

    const outside = document.createElement('input')
    document.body.append(outside)
    outside.focus()
    pointerDownOn(outside)
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(outside)
    outside.remove()
    wrapper.unmount()
  })

  it('点浮层内部不关闭', async () => {
    const wrapper = mountHost(true)
    await wrapper.vm.$nextTick()

    pointerDownOn(wrapper.find('.sp-input').element)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('插槽拿到的 close 能关闭并归还焦点', async () => {
    const wrapper = mountHost()
    const trigger = wrapper.find('button')
    await trigger.trigger('click')
    await wrapper.vm.$nextTick()

    await wrapper.find('.sp-done').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  /* 卸载时没摘掉 document 上的监听，之后每次按键都会打到已销毁的组件上。 */
  it('卸载后不再响应文档事件', async () => {
    const wrapper = mountHost(true)
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    expect(() => {
      pressEscape()
      pointerDownOn(document.body)
    }).not.toThrow()
  })
})
