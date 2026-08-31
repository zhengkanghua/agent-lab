import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseButton from '@/shared/ui/BaseButton.vue'

/* 断言的是「换成 props 之后行为没丢」，不是样式长什么样。
 * 颜色由 shared-styles.node.spec.ts 那边守 token 分层，这里不重复。
 */

describe('BaseButton', () => {
  it('默认渲染 button 且 type=button', () => {
    const wrapper = mount(BaseButton, { slots: { default: '发送' } })
    expect(wrapper.element.tagName).toBe('BUTTON')
    expect(wrapper.attributes('type')).toBe('button')
    expect(wrapper.text()).toBe('发送')
  })

  it('variant 与 size 落到 class 上', () => {
    const wrapper = mount(BaseButton, { props: { variant: 'primary', size: 'sm' } })
    expect(wrapper.classes()).toContain('is-primary')
    expect(wrapper.classes()).toContain('is-sm')
  })

  it('loading 时禁用、标 aria-busy、并显示转圈', () => {
    const wrapper = mount(BaseButton, { props: { loading: true }, slots: { default: '提交' } })
    // 关键行为：loading 也要挡住点击，否则双击会发两次请求。
    expect(wrapper.attributes('disabled')).toBeDefined()
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.find('.base-spinner').exists()).toBe(true)
    // 文案保留，按钮宽度不跳。
    expect(wrapper.text()).toContain('提交')
  })

  it('loading 时用转圈替掉 icon 插槽，不并排显示两个图标', () => {
    const wrapper = mount(BaseButton, {
      props: { loading: true },
      slots: { icon: '<svg class="my-icon" />' },
    })
    expect(wrapper.find('.my-icon').exists()).toBe(false)
    expect(wrapper.find('.base-spinner').exists()).toBe(true)
  })

  it('非 loading 时渲染 icon 插槽且没有转圈', () => {
    const wrapper = mount(BaseButton, { slots: { icon: '<svg class="my-icon" />' } })
    expect(wrapper.find('.my-icon').exists()).toBe(true)
    expect(wrapper.find('.base-spinner').exists()).toBe(false)
  })

  it('disabled 时不标 aria-busy——它不是「忙」，是「不能用」', () => {
    const wrapper = mount(BaseButton, { props: { disabled: true } })
    expect(wrapper.attributes('disabled')).toBeDefined()
    expect(wrapper.attributes('aria-busy')).toBeUndefined()
  })

  it('禁用时不触发 click', async () => {
    let clicks = 0
    const wrapper = mount(BaseButton, {
      props: { disabled: true, onClick: () => (clicks += 1) },
    })
    await wrapper.trigger('click')
    expect(clicks).toBe(0)
  })

  it('type=submit 透传，表单按钮才能提交', () => {
    const wrapper = mount(BaseButton, { props: { type: 'submit' } })
    expect(wrapper.attributes('type')).toBe('submit')
  })

  it('给了 to 就渲染链接而不是 button，且带上变体 class', () => {
    const wrapper = mount(BaseButton, {
      props: { to: '/admin', variant: 'ghost' },
      slots: { default: '用户管理' },
      global: {
        stubs: {
          RouterLink: {
            props: ['to'],
            template: '<a class="stub-link" :href="to"><slot /></a>',
          },
        },
      },
    })
    const link = wrapper.get('a.stub-link')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(link.attributes('href')).toBe('/admin')
    expect(link.text()).toBe('用户管理')
    // 链接形态也要吃到变体样式，否则顶栏那几个入口会掉回裸 <a>。
    expect(link.classes()).toContain('is-ghost')
    expect(link.classes()).toContain('base-button')
  })

  it('iconOnly 与 block 各自落 class', () => {
    const wrapper = mount(BaseButton, { props: { iconOnly: true, block: true } })
    expect(wrapper.classes()).toContain('is-icon-only')
    expect(wrapper.classes()).toContain('is-block')
  })
})
