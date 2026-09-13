import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import BaseDisclosure from '@/shared/ui/BaseDisclosure.vue'

/* jsdom 不会因为点击 summary 就翻转 details.open——那是浏览器的行为。
   所以这里改 DOM 的 open 再手动派发 toggle，模拟浏览器改完才通知的顺序。 */
function simulateBrowserToggle(el: HTMLDetailsElement, next: boolean): void {
  el.open = next
  el.dispatchEvent(new Event('toggle'))
}

describe('BaseDisclosure', () => {
  it('渲染摘要、图标和正文插槽', () => {
    const wrapper = mount(BaseDisclosure, {
      props: { summary: '查看返回内容' },
      slots: { default: '<pre>tool output</pre>', icon: '<svg class="probe" />' },
    })

    expect(wrapper.find('summary').text()).toContain('查看返回内容')
    expect(wrapper.find('summary .probe').exists()).toBe(true)
    expect(wrapper.find('pre').text()).toBe('tool output')
  })

  it('不传 open 时默认收起', () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要' } })

    expect(wrapper.find('details').element.open).toBe(false)
  })

  it('受控模式下跟随 open 变化', async () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要', open: false } })

    expect(wrapper.find('details').element.open).toBe(false)

    await wrapper.setProps({ open: true })
    expect(wrapper.find('details').element.open).toBe(true)
  })

  it('受控模式下用户展开会回传 update:open', () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要', open: false } })

    simulateBrowserToggle(wrapper.find('details').element, true)

    expect(wrapper.emitted('update:open')).toEqual([[true]])
  })

  /* 外部刚把 open 改成 true，浏览器随后派发的 toggle 也是 true。
     这时再 emit 一次就会让父组件重复赋值，受控端容易绕成环。 */
  it('状态与 open 一致时不重复回传', async () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要', open: false } })

    await wrapper.setProps({ open: true })
    simulateBrowserToggle(wrapper.find('details').element, true)

    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('非受控模式不回传事件', () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要' } })

    simulateBrowserToggle(wrapper.find('details').element, true)

    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('meta 只在传入时渲染', async () => {
    const wrapper = mount(BaseDisclosure, { props: { summary: '摘要' } })
    expect(wrapper.find('.disclosure-meta').exists()).toBe(false)

    await wrapper.setProps({ meta: '3 次调用' })
    expect(wrapper.find('.disclosure-meta').text()).toBe('3 次调用')
  })
})
