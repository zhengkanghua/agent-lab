import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AgentTurnCard from '../components/AgentTurnCard.vue'
import { createTurn, type AgentTurn } from '../model/conversation'

function turn(overrides: Partial<AgentTurn> = {}): AgentTurn {
  return { ...createTurn('央行利率怎么走？'), ...overrides }
}

function mountCard(overrides: Partial<AgentTurn> = {}, canRetry = true) {
  return mount(AgentTurnCard, { props: { turn: turn(overrides), canRetry } })
}

describe('AgentTurnCard', () => {
  it('还没收到 token 时显示占位，不留一张空白答案卡', () => {
    const wrapper = mountCard()

    expect(wrapper.get('.thinking').text()).toContain('正在思考')
    expect(wrapper.find('.answer-body').exists()).toBe(false)
  })

  it('有回答后换成正文，并在流式期间带上流式标记', () => {
    const wrapper = mountCard({ answer: '预计维持不变。' })

    expect(wrapper.find('.thinking').exists()).toBe(false)
    expect(wrapper.get('.answer-body').classes()).toContain('is-streaming')
  })

  it('结束后去掉流式标记', () => {
    const wrapper = mountCard({ answer: '预计维持不变。', status: 'done' })

    expect(wrapper.get('.answer-body').classes()).not.toContain('is-streaming')
  })

  it('提问按纯文本渲染，回答按 Markdown 渲染', () => {
    const wrapper = mountCard({
      question: '**不是粗体**',
      answer: '**是粗体**',
      status: 'done',
    })

    /* 两侧口径不同，这是有意的：提问是用户原文，他打的星号就是星号；
       回答是模型输出，Markdown 是它的表达方式。
       回答那侧的注入面由 MarkdownAnswer.spec.ts 单独盯（不装 rehype-raw + 开 sanitize）。 */
    expect(wrapper.get('.question-text').text()).toBe('**不是粗体**')
    expect(wrapper.find('.question-text strong').exists()).toBe(false)
    expect(wrapper.get('.answer-body strong').text()).toBe('是粗体')
  })

  it('回答里的裸 HTML 不进 DOM', () => {
    const wrapper = mountCard({ answer: '<img src=x onerror="alert(1)">', status: 'done' })

    // 走 Markdown 之后这条尤其要守住：整页只有这一处不是纯文本插值。
    expect(wrapper.find('.answer-body img').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('onerror')
  })

  it('取消的一轮标出已停止', () => {
    const wrapper = mountCard({ answer: '半句', status: 'cancelled' })

    expect(wrapper.get('.turn-state').text()).toBe('已停止')
  })

  it('错误块给出标题与下一步，并按 role=alert 播报', () => {
    const wrapper = mountCard({
      status: 'error',
      error: { title: '模型响应超时', description: '可以稍后重发本轮提问。', retryable: true },
    })

    expect(wrapper.get('.turn-error').attributes('role')).toBe('alert')
    expect(wrapper.text()).toContain('模型响应超时')
    expect(wrapper.text()).toContain('可以稍后重发本轮提问。')
    expect(wrapper.get('.turn').classes()).toContain('is-error')
  })

  it('可重试的错误给出重发按钮', async () => {
    const wrapper = mountCard({
      status: 'error',
      error: { title: '模型响应超时', description: '稍后重发。', retryable: true },
    })

    await wrapper.get('.retry-button').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('不可重试的错误不给按钮', () => {
    const wrapper = mountCard({
      status: 'error',
      error: { title: 'Agent 尚未就绪', description: '服务端配置需要维护。', retryable: false },
    })

    expect(wrapper.find('.retry-button').exists()).toBe(false)
  })

  it('不是最后一轮时即使错误可重试也不给按钮', () => {
    const wrapper = mountCard(
      {
        status: 'error',
        error: { title: '模型响应超时', description: '稍后重发。', retryable: true },
      },
      false,
    )

    expect(wrapper.find('.retry-button').exists()).toBe(false)
  })

  it('工具轨迹排在回答上方', () => {
    const wrapper = mountCard({
      answer: '维持不变。',
      status: 'done',
      traces: [
        {
          id: 'trace-1',
          toolCallId: 'call-1',
          tool: 'search_news',
          arguments: { query: '利率' },
          content: '找到 2 篇。',
          failed: false,
        },
      ],
    })

    const html = wrapper.html()
    expect(html.indexOf('trace-block')).toBeLessThan(html.indexOf('answer-body'))
  })
})
