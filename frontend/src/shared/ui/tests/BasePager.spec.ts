import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BasePager from '../BasePager.vue'

/* 分页器：六处目录原本各写一遍，这里把差异面钉住——
   页码可以不给（窄容器里塞不下）、请求在途时两枚键都禁用、两个事件分得清。 */

function mountPager(props: Record<string, unknown> = {}) {
  return mount(BasePager, {
    props: { hasPrevious: true, hasMore: true, ...props },
  })
}

function buttons(wrapper: ReturnType<typeof mountPager>) {
  return wrapper.findAll('button')
}

describe('BasePager', () => {
  it('给出页码时渲染「第 N 页」', () => {
    const wrapper = mountPager({ page: 3 })

    expect(wrapper.get('.page-number').text()).toBe('第 3 页')
  })

  it('不给页码就不渲染页码', () => {
    const wrapper = mountPager()

    expect(wrapper.find('.page-number').exists()).toBe(false)
    expect(buttons(wrapper).map((button) => button.text())).toEqual(['上一页', '下一页'])
  })

  it('按 hasPrevious / hasMore 分别禁用两枚键，而不是一起禁用', () => {
    const wrapper = mountPager({ hasPrevious: false, hasMore: true })
    const [previous, next] = buttons(wrapper)

    expect(previous!.attributes('disabled')).toBeDefined()
    expect(next!.attributes('disabled')).toBeUndefined()
  })

  it('请求在途时两枚键都禁用（避免连点翻页）', () => {
    const wrapper = mountPager({ busy: true })
    const [previous, next] = buttons(wrapper)

    expect(previous!.attributes('disabled')).toBeDefined()
    expect(next!.attributes('disabled')).toBeDefined()
  })

  it('两个事件分得清：点哪枚键发哪个', async () => {
    const wrapper = mountPager()
    const [previous, next] = buttons(wrapper)

    await previous!.trigger('click')
    await next!.trigger('click')

    expect(wrapper.emitted('previous')).toHaveLength(1)
    expect(wrapper.emitted('next')).toHaveLength(1)
  })

  it('默认贴右，align="start" 时贴左', () => {
    const end = mountPager().get('.pager')
    const start = mountPager({ align: 'start' }).get('.pager')

    expect(end.classes()).not.toContain('is-start')
    expect(start.classes()).toContain('is-start')
  })

  it('额外动作（如用量表的刷新）排在翻页键之后', () => {
    const wrapper = mount(BasePager, {
      props: { hasPrevious: true, hasMore: true },
      slots: { default: '<button type="button">刷新</button>' },
    })

    expect(buttons(wrapper).map((button) => button.text())).toEqual(['上一页', '下一页', '刷新'])
  })
})
