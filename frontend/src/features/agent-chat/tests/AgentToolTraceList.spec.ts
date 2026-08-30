import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AgentToolTraceList from '../components/AgentToolTraceList.vue'
import type { AgentToolTrace } from '../model/conversation'

function trace(overrides: Partial<AgentToolTrace> = {}): AgentToolTrace {
  return {
    id: 'trace-1',
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

    expect(wrapper.find('.spin').exists()).toBe(true)
    expect(wrapper.text()).toContain('执行中')
    expect(wrapper.find('.trace-output').exists()).toBe(false)
  })

  it('完成后能展开看到工具原文', () => {
    const wrapper = mount(AgentToolTraceList, { props: { traces: [trace()] } })

    expect(wrapper.find('.spin').exists()).toBe(false)
    expect(wrapper.get('.trace-output pre').text()).toBe('找到 2 篇。')
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
