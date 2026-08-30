import { describe, expect, it } from 'vitest'
import {
  appendToolCall,
  applyToolResult,
  createTurn,
  nextLocalId,
  settlePendingTraces,
} from '../model/conversation'

describe('conversation', () => {
  it('新一轮从「流式中」开始，没有回答也没有错误', () => {
    const turn = createTurn('央行利率')

    expect(turn).toMatchObject({
      question: '央行利率',
      answer: '',
      traces: [],
      error: null,
      status: 'streaming',
    })
  })

  it('本地 id 单调递增，同一轮里的相同调用不会撞 key', () => {
    const first = nextLocalId('trace')
    const second = nextLocalId('trace')

    expect(first).not.toBe(second)
    expect(first.startsWith('trace-')).toBe(true)
  })

  it('工具调用缺省参数时记成空对象，界面不必再判 undefined', () => {
    const turn = createTurn('问题')

    appendToolCall(turn, { event: 'tool_call', tool: 'read_document' })

    expect(turn.traces[0]).toMatchObject({
      tool: 'read_document',
      arguments: {},
      content: null,
      failed: false,
    })
  })

  it('结果并进同名且未完成的最早一条', () => {
    const turn = createTurn('问题')
    appendToolCall(turn, { event: 'tool_call', tool: 'search_news', arguments: { query: '甲' } })
    appendToolCall(turn, { event: 'tool_call', tool: 'search_news', arguments: { query: '乙' } })

    applyToolResult(turn, {
      event: 'tool_result',
      tool: 'search_news',
      content: '甲的结果',
      failed: false,
    })

    expect(turn.traces[0]?.content).toBe('甲的结果')
    expect(turn.traces[1]?.content).toBeNull()
  })

  it('不同工具的结果不会串到别的工具上', () => {
    const turn = createTurn('问题')
    appendToolCall(turn, { event: 'tool_call', tool: 'search_news' })
    appendToolCall(turn, { event: 'tool_call', tool: 'read_document' })

    applyToolResult(turn, {
      event: 'tool_result',
      tool: 'read_document',
      content: '全文。',
      failed: false,
    })

    expect(turn.traces[0]?.content).toBeNull()
    expect(turn.traces[1]?.content).toBe('全文。')
  })

  it('找不到对应调用时补一条只有结果的轨迹', () => {
    const turn = createTurn('问题')

    applyToolResult(turn, {
      event: 'tool_result',
      tool: 'search_news',
      content: '孤立结果',
      failed: false,
    })

    // 宁可显示一条来源不明的工具结果，也不要让用户以为模型没查资料。
    expect(turn.traces).toHaveLength(1)
    expect(turn.traces[0]).toMatchObject({ content: '孤立结果', arguments: {} })
  })

  it('收尾只动还在执行中的轨迹，已完成的保持原样', () => {
    const turn = createTurn('问题')
    appendToolCall(turn, { event: 'tool_call', tool: 'search_news' })
    appendToolCall(turn, { event: 'tool_call', tool: 'read_document' })
    applyToolResult(turn, {
      event: 'tool_result',
      tool: 'search_news',
      content: '查到了。',
      failed: false,
    })

    settlePendingTraces(turn, '已取消。')

    expect(turn.traces[0]).toMatchObject({ content: '查到了。', failed: false })
    expect(turn.traces[1]).toMatchObject({ content: '已取消。', failed: true })
  })
})
