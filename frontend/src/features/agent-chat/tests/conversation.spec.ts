import { describe, expect, it } from 'vitest'
import {
  appendToolCall,
  applyToolResult,
  createTurn,
  nextLocalId,
  settlePendingTraces,
  turnsFromReplay,
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

  describe('turnsFromReplay', () => {
    it('保持轮次顺序，全部标成 done 且不带 error', () => {
      const turns = turnsFromReplay(
        [
          { question: '第一问', answer: '第一答' },
          { question: '第二问', answer: '第二答' },
        ],
        '未送达。',
      )

      expect(turns.map((turn) => [turn.question, turn.answer])).toEqual([
        ['第一问', '第一答'],
        ['第二问', '第二答'],
      ])
      expect(turns.every((turn) => turn.status === 'done' && turn.error === null)).toBe(true)
    })

    it('每一轮和每条轨迹都拿到互不相同的本地 id', () => {
      // id 只做 v-for 的 key。重复的 key 会让 Vue 复用错误的 DOM 节点，表现是切会话后
      // 某一轮的内容留在原地不更新。
      const turns = turnsFromReplay(
        [
          { question: '甲', answer: '', traces: [{ tool: 'search_news' }] },
          { question: '乙', answer: '', traces: [{ tool: 'search_news' }] },
        ],
        '未送达。',
      )

      const ids = [...turns.map((turn) => turn.id), ...turns.flatMap((t) => t.traces.map((x) => x.id))]
      expect(new Set(ids).size).toBe(ids.length)
    })

    it('answer 为空串原样保留，不补一句假回答', () => {
      const [turn] = turnsFromReplay([{ question: '没答成', answer: '' }], '未送达。')

      expect(turn?.answer).toBe('')
      expect(turn?.status).toBe('done')
    })

    it('没有结果的轨迹用给定说明收尾并标成失败', () => {
      const [turn] = turnsFromReplay(
        [
          {
            question: '查一下',
            answer: '查不到。',
            traces: [
              { tool: 'search_news', content: '有结果', failed: false },
              { tool: 'search_news', content: null },
            ],
          },
        ],
        '当时中断了。',
      )

      expect(turn?.traces[0]).toMatchObject({ content: '有结果', failed: false })
      expect(turn?.traces[1]).toMatchObject({ content: '当时中断了。', failed: true })
    })

    it('失败的轨迹保留失败标记', () => {
      const [turn] = turnsFromReplay(
        [
          {
            question: '查一下',
            answer: '暂时查不了。',
            traces: [{ tool: 'search_news', content: '工具调用失败。', failed: true }],
          },
        ],
        '未送达。',
      )

      expect(turn?.traces[0]?.failed).toBe(true)
    })

    it('traces 缺省或为 null 时得到空数组，不是 undefined', () => {
      // 组件里对它 v-for，undefined 会直接抛。
      const turns = turnsFromReplay(
        [
          { question: '甲', answer: '答' },
          { question: '乙', answer: '答', traces: null },
        ],
        '未送达。',
      )

      expect(turns[0]?.traces).toEqual([])
      expect(turns[1]?.traces).toEqual([])
    })

    it('arguments 缺省时补空对象，不把 null 交给组件', () => {
      const [turn] = turnsFromReplay(
        [{ question: '查', answer: '答', traces: [{ tool: 'search_news', content: 'ok' }] }],
        '未送达。',
      )

      expect(turn?.traces[0]?.arguments).toEqual({})
    })

    it('空历史得到空数组', () => {
      expect(turnsFromReplay([], '未送达。')).toEqual([])
    })
  })
})
