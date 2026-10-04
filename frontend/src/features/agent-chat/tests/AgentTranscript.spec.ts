import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AgentTranscript from '../components/AgentTranscript.vue'
import { createTurn, turnsFromReplay, type AgentTurn } from '../model/conversation'

const EXAMPLES = ['最近有哪些关于利率的报道？', '把这条新闻的全文读一下'] as const

/* 分界线上那句话逐字写在这里（不从组件导入）：它是要被钉住的验收文案，
   从被测代码里拿常量就等于两边一起漂，钉不住。 */
const MEMORY_BOUNDARY_TEXT =
  '此处之前的对话，模型只保留了摘要；此处之后的问答与工具轨迹完整保留，但较早的工具原文可能已被清理成占位文字。'

function mountTranscript(turns: AgentTurn[] = [], streaming = false) {
  return mount(AgentTranscript, { props: { turns, streaming, examples: EXAMPLES } })
}

function doneTurn(question: string): AgentTurn {
  return { ...createTurn(question), answer: '回答。', status: 'done' }
}

describe('AgentTranscript', () => {
  it('空态是一句标题加建议卡，没有品牌装饰', () => {
    const wrapper = mountTranscript()

    /* Q9：空态只有一句标题 + 建议卡，不放品牌元素。原来那个圆形图标是装饰性品牌位，
       撤掉了。「回答可能有误、只读数据」两条挪到了输入区下方的细则行——它们对每一轮
       都成立，只挂在空态等于答案出现后就不再提醒。那两条现在由
       AgentChatPage.spec.ts 盯。 */
    expect(wrapper.get('.empty-state h3').text()).toBe('今天想查什么？')
    expect(wrapper.findAll('.suggestion-button')).toHaveLength(EXAMPLES.length)
    expect(wrapper.find('.empty-icon').exists()).toBe(false)
  })

  it('点示例问题把原文交给上层', async () => {
    const wrapper = mountTranscript()

    await wrapper.findAll('.suggestion-button')[1]?.trigger('click')

    expect(wrapper.emitted('choose-example')?.[0]).toEqual([EXAMPLES[1]])
  })

  it('有历史后不再显示空态', () => {
    const wrapper = mountTranscript([doneTurn('第一问')])

    expect(wrapper.find('.empty-state').exists()).toBe(false)
    expect(wrapper.findAll('.turn')).toHaveLength(1)
  })

  it('按顺序渲染每一轮', () => {
    const wrapper = mountTranscript([doneTurn('第一问'), doneTurn('第二问')])

    const questions = wrapper.findAll('.question-text').map((node) => node.text())
    expect(questions).toEqual(['第一问', '第二问'])
  })

  it('有分界标记时，线画在对应那一轮之后，那句话也在', () => {
    const turns = turnsFromReplay(
      [
        { question: '第一问', answer: '答', run_id: 'run-1' },
        { question: '第二问', answer: '答', run_id: 'run-2' },
        { question: '第三问', answer: '答', run_id: 'run-3' },
      ],
      '未送达。',
      'run-2',
    )
    const wrapper = mountTranscript(turns)

    // 看渲染出来的结构：线是「第二问」之后、第三问之前的那个子节点，既不在列表头部也不在末尾。
    const nodes = Array.from(wrapper.get('.turn-list').element.children)
    const boundary = nodes.findIndex((node) => node.classList.contains('memory-boundary'))
    const secondTurn = nodes.findIndex((node) => node.textContent?.includes('第二问'))
    expect(boundary).toBe(secondTurn + 1)
    expect(boundary).toBeGreaterThan(0)
    expect(boundary).toBeLessThan(nodes.length - 1)
    // 线和那句话是同一个元素（线就是这段文字的上边框），两者只能同时出现。
    expect(wrapper.get('.memory-boundary').text()).toBe(MEMORY_BOUNDARY_TEXT)
  })

  it('没有分界标记时，线和那句话都不出现', () => {
    const wrapper = mountTranscript([doneTurn('第一问'), doneTurn('第二问')])

    expect(wrapper.findAll('.turn')).toHaveLength(2)
    expect(wrapper.find('.memory-boundary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('模型只保留了摘要')
  })

  it('分界标记对不上任何一轮时，线和那句话都不出现', () => {
    // 标记指向的那一轮可能因强杀或清空根本没落进表里。宁可不画，也不能把线画在一个猜出来的
    // 位置上——那会让用户以为线以上模型还记得，而事实未知。
    const turns = turnsFromReplay(
      [{ question: '第一问', answer: '答', run_id: 'run-1' }],
      '未送达。',
      'run-9',
    )
    const wrapper = mountTranscript(turns)

    expect(turns.every((turn) => !turn.isMemoryBoundary)).toBe(true)
    expect(wrapper.find('.memory-boundary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('模型只保留了摘要')
  })

  it('只有最后一轮能重发', () => {
    const failed = (question: string): AgentTurn => ({
      ...createTurn(question),
      status: 'error',
      error: { title: '模型响应超时', description: '稍后重发。', retryable: true },
    })
    const wrapper = mountTranscript([failed('第一问'), failed('第二问')])

    // 重发的语义是「接着当前历史再问一次」，中间轮次没有这个语义。
    expect(wrapper.findAll('.retry-button')).toHaveLength(1)
  })

  it('流式期间不给重发按钮', () => {
    const streamingTurn: AgentTurn = {
      ...createTurn('问题'),
      status: 'error',
      error: { title: '模型响应超时', description: '稍后重发。', retryable: true },
    }
    const wrapper = mountTranscript([streamingTurn], true)

    expect(wrapper.find('.retry-button').exists()).toBe(false)
    expect(wrapper.get('.transcript').attributes('aria-busy')).toBe('true')
  })

  it('重发事件冒泡到上层', async () => {
    const failed: AgentTurn = {
      ...createTurn('问题'),
      status: 'error',
      error: { title: '模型响应超时', description: '稍后重发。', retryable: true },
    }
    const wrapper = mountTranscript([failed])

    await wrapper.get('.retry-button').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})
