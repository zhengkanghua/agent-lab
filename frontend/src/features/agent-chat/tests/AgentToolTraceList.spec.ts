import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import AgentToolTraceList from '../components/AgentToolTraceList.vue'
import type { AgentToolTrace } from '../model/conversation'

function trace(overrides: Partial<AgentToolTrace> = {}): AgentToolTrace {
  return {
    id: 'trace-1',
    toolCallId: 'call-1',
    tool: 'search_news',
    arguments: { query: '央行利率' },
    content: '找到 2 篇。',
    failed: false,
    ...overrides,
  }
}

describe('AgentToolTraceList', () => {
  it('没有轨迹时整块不渲染', () => {
    expect(
      mount(AgentToolTraceList, { props: { traces: [] } })
        .find('.trace-list')
        .exists(),
    ).toBe(false)
  })

  it('已知工具名显示中文说明', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: {
        traces: [trace(), trace({ id: 'trace-2', tool: 'read_document', arguments: {} })],
      },
    })

    expect(wrapper.text()).toContain('检索新闻')
    expect(wrapper.text()).toContain('读取全文')
  })

  it('未知工具名原样显示，不猜也不隐藏', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ tool: 'some_future_tool' })] },
    })

    expect(wrapper.text()).toContain('some_future_tool')
  })

  it('执行中显示转圈且不给返回内容折叠块', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: null })] },
    })

    // 断言组件而不是 .spin 类名：转圈归 BaseSpinner，类名是它的实现细节。
    expect(wrapper.findComponent(BaseSpinner).exists()).toBe(true)
    expect(wrapper.text()).toContain('执行中')
    expect(wrapper.find('.trace-output').exists()).toBe(false)
  })

  it('完成后给出折叠的工具原文，默认收起', () => {
    const wrapper = mount(AgentToolTraceList, { props: { traces: [trace()] } })

    expect(wrapper.findComponent(BaseSpinner).exists()).toBe(false)
    expect(wrapper.get('.trace-output summary').text()).toContain('查看返回内容')
    expect(wrapper.get('.trace-output pre').text()).toBe('找到 2 篇。')
    // 收起的 details 里子节点仍在 DOM 中，所以「取到了 pre」证明不了展开状态，
    // 得直接读 open。默认收起是这里的实际契约：一屏里可能有好几条轨迹。
    expect(wrapper.get<HTMLDetailsElement>('details.trace-output').element.open).toBe(false)
  })

  it('失败的轨迹带上失败标记与状态字', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ failed: true, content: '数据库暂时不可用。' })] },
    })

    expect(wrapper.get('.trace-item').classes()).toContain('is-failed')
    expect(wrapper.text()).toContain('未成功')
  })

  it('参数拍成一行，嵌套结构折叠掉', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: {
        traces: [
          trace({
            arguments: { query: '利率', limit: 5, recent: true, cursor: null, filters: { a: 1 } },
          }),
        ],
      },
    })

    expect(wrapper.get('.trace-arguments').text()).toBe(
      'query=利率，limit=5，recent=true，cursor=空，filters=…',
    )
  })

  it('没有参数时不留一行空的参数区', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ arguments: {} })] },
    })

    expect(wrapper.find('.trace-arguments').exists()).toBe(false)
  })

  it('工具返回的文本按纯文本渲染', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: '<img src=x onerror=alert(1)>' })] },
    })

    // 工具返回的内容里可能含 RSS 抓来的外部文本，注入 HTML 会直接变成 XSS。
    expect(wrapper.get('.trace-output pre').text()).toBe('<img src=x onerror=alert(1)>')
    expect(wrapper.find('.trace-output img').exists()).toBe(false)
  })
})

/* Q10：轨迹整块折叠成一行摘要，流式中展开、落定收起。
   `details.trace-block` 是外层那个折叠块；`details.trace-output` 是每条轨迹里
   「查看返回内容」的内层折叠块，两者不要混。 */
describe('AgentToolTraceList 折叠', () => {
  const outerOpen = (wrapper: ReturnType<typeof mount>) =>
    wrapper.get<HTMLDetailsElement>('details.trace-block').element.open

  /* 每条轨迹里还有一个「查看返回内容」的内层折叠，它同样带 .disclosure-title，
     所以先取到外层那个 summary 再往里找，别用全局选择器。
     另外别写成 `.trace-block > summary .disclosure-title` 一整条：jsdom 的选择器引擎
     在这个三段式上返回空集（换成 `details > summary .disclosure-title` 就能命中），
     分两步取反而稳。 */
  const summaryOf = (wrapper: ReturnType<typeof mount>) => wrapper.get('.trace-block > summary')

  it('摘要行写出调用了哪些工具，而不是只报数量', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace(), trace({ id: 't2', tool: 'read_document' })] },
    })

    // 折叠后这一行是唯一可见的轨迹信息，只写「2 次调用」的话用户还得展开才知道干了什么。
    expect(summaryOf(wrapper).get('.disclosure-title').text()).toBe('检索新闻 · 读取全文')
  })

  it('同名工具连着调多次时合并计数', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace(), trace({ id: 't2' }), trace({ id: 't3' })] },
    })

    expect(summaryOf(wrapper).get('.disclosure-title').text()).toBe('检索新闻 ×3')
  })

  it('流式中默认展开', () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: null })], streaming: true },
    })

    expect(outerOpen(wrapper)).toBe(true)
  })

  it('落定后自动收起', async () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: null })], streaming: true },
    })

    await wrapper.setProps({ traces: [trace()], streaming: false })

    expect(outerOpen(wrapper)).toBe(false)
  })

  it('有失败的轨迹时落定不收起', async () => {
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: null })], streaming: true },
    })

    await wrapper.setProps({
      traces: [trace({ failed: true, content: '数据库暂时不可用。' })],
      streaming: false,
    })

    // 失败那条要让用户直接看见，而不是先展开再找。
    expect(outerOpen(wrapper)).toBe(true)
  })

  it('非流式挂载时默认收起', () => {
    const wrapper = mount(AgentToolTraceList, { props: { traces: [trace()] } })

    expect(outerOpen(wrapper)).toBe(false)
  })

  it('流式期间用户手动收起，不会被下一个 token 掀开', async () => {
    /* 这条钉住「用本地 ref 承接」而不是把 open 直接算成 streaming。
       后者每次重渲染都会把用户的手动收起推翻。 */
    const wrapper = mount(AgentToolTraceList, {
      props: { traces: [trace({ content: null })], streaming: true },
    })
    const details = wrapper.get<HTMLDetailsElement>('details.trace-block')

    details.element.open = false
    await details.trigger('toggle')
    // 模拟流式期间又来了一条轨迹。
    await wrapper.setProps({ traces: [trace({ content: null }), trace({ id: 't2' })] })

    expect(outerOpen(wrapper)).toBe(false)
  })

  it('摘要右侧报执行中与失败数，都正常时不写字', () => {
    const meta = (wrapper: ReturnType<typeof mount>) => summaryOf(wrapper).find('.disclosure-meta')

    const running = mount(AgentToolTraceList, { props: { traces: [trace({ content: null })] } })
    expect(meta(running).text()).toBe('执行中')

    const failed = mount(AgentToolTraceList, {
      props: { traces: [trace({ failed: true }), trace({ id: 't2', content: null })] },
    })
    // 失败优先于执行中：收起状态下只有这一个位置能报，一条已失败比「还有一条在跑」更该被看见。
    expect(meta(failed).text()).toBe('1 项未成功')

    const clean = mount(AgentToolTraceList, { props: { traces: [trace()] } })
    // 「成功」是默认预期，写出来是噪音。
    expect(meta(clean).exists()).toBe(false)
  })
})
