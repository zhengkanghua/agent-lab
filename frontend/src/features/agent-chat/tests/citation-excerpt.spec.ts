import { describe, expect, it } from 'vitest'
import { citationMarkerRegExp, excerptFor, EVIDENCE_BODY_MARKERS } from '../model/citation-excerpt'
import type { AgentToolTrace } from '../model/conversation'

/* 夹具刻意用**从真实工具输出抄来的字面量**，不另造一套格式。
 *
 * 面板按后端拼工具正文的标记切块，这是一条隐式契约：拼法一改（哪怕只改一个冒号），面板就会
 * 静默回落成打开当前全文，而且不报错。手写夹具只能证明切块自洽，抄来的字面量才可能暴露漂移。
 * 两处拼法的出处：
 *   - backend/src/agent_lab/agent/tools/search_documents.py（一次检索命中多篇 = 一份正文里多块）
 *   - backend/src/agent_lab/agent/tools/read_document.py（当前正文）
 */
const SEARCH_CONTENT = `本次检索范围：新闻

[[E0123456789ab]] 央行下调再贷款利率
document_id: 20000000-0000-4000-8000-000000000001
知识库: 新闻
来源: 财经日报 | 发布: 2026-08-18
实际命中片段:
央行今日宣布下调再贷款利率 0.25 个百分点。此次调整自下月起生效。

[[E0b1c2d3e4f5a]] 存量房贷利率怎么走
document_id: 20000000-0000-4000-8000-000000000002
知识库: 新闻
来源: 无外部来源 | 发布: 发布时间未知
实际命中片段:
多家银行表示，存量房贷利率将在下月同步调整。`

const READ_CONTENT = `[[E0aaaaaaaaaaa]] 标题: 运行手册
document_id: 20000000-0000-4000-8000-000000000003
知识库: 运维
来源: 运行手册.md | 发布: 2026-07-01 09:30
作者: 未署名
原文地址: 无外部地址

当前正文:
备份保留 7 天，清理任务每天凌晨两点执行。`

function trace(content: string | null, id = 'trace-1'): AgentToolTrace {
  return {
    id,
    toolCallId: null,
    tool: 'search_documents',
    arguments: {},
    content,
    failed: false,
  }
}

describe('excerptFor', () => {
  it('检索结果来源对上「实际命中片段」之后那段', () => {
    const excerpt = excerptFor('E0123456789ab', [trace(SEARCH_CONTENT)])

    expect(excerpt).toEqual({
      body: '央行今日宣布下调再贷款利率 0.25 个百分点。此次调整自下月起生效。',
      traceId: 'trace-1',
    })
  })

  it('全文读取来源对上「当前正文」之后那段，元数据行不进主体', () => {
    const excerpt = excerptFor('E0aaaaaaaaaaa', [trace(READ_CONTENT)])

    expect(excerpt?.body).toBe('备份保留 7 天，清理任务每天凌晨两点执行。')
    expect(excerpt?.body).not.toContain('原文地址')
    expect(excerpt?.body).not.toContain('作者')
  })

  it('一份正文里拼了多个块时，按引用取到对应那一块', () => {
    const traces = [trace(SEARCH_CONTENT)]

    expect(excerptFor('E0b1c2d3e4f5a', traces)?.body).toBe(
      '多家银行表示，存量房贷利率将在下月同步调整。',
    )
    /* 第 1 块不能被第 2 块的内容污染：多篇命中时拿错块等于给用户看另一篇的正文。 */
    expect(excerptFor('E0123456789ab', traces)?.body).toContain('央行今日宣布')
  })

  it('跨多条轨迹找引用：检索那条与全文那条各归各的', () => {
    const traces = [trace(SEARCH_CONTENT, 'trace-search'), trace(READ_CONTENT, 'trace-read')]

    expect(excerptFor('E0123456789ab', traces)?.traceId).toBe('trace-search')
    expect(excerptFor('E0aaaaaaaaaaa', traces)?.traceId).toBe('trace-read')
  })

  it('这一轮没有含该标记的 Tool 结果时返回 null', () => {
    expect(excerptFor('E0123456789ab', [])).toBeNull()
    expect(excerptFor('E0123456789ab', [trace('这次调用没有留下结果记录。')])).toBeNull()
    expect(excerptFor('E0123456789ab', [trace(null)])).toBeNull()
  })

  it('有标记但切不出这一块时返回 null', () => {
    /* 标记不在行首：后端每块都以行首标记起头，这种形状说明拼法已经变了。 */
    const content = `说明见 [[E0123456789ab]] 这一行\n实际命中片段:\n央行今日宣布下调再贷款利率。`

    expect(excerptFor('E0123456789ab', [trace(content)])).toBeNull()
  })

  it('块里取不到主体时返回 null', () => {
    const content = `[[E0123456789ab]] 央行下调再贷款利率
document_id: 20000000-0000-4000-8000-000000000001
知识库: 新闻
来源: 财经日报 | 发布: 2026-08-18
实际命中片段:
   `

    expect(excerptFor('E0123456789ab', [trace(content)])).toBeNull()
  })

  it('被历史清理过的正文照原样返回，含占位文字也不降级', () => {
    /* 占位文字是对外文案（backend/src/agent_lab/agent/limits.py 的注释写明「会被用户看到」），
       显示它等于如实说这段被截过；这里跳去打开当前全文，看到的反而不再是当初那段。 */
    const pruned = `[[E0123456789ab]] 央行下调再贷款利率
document_id: 20000000-0000-4000-8000-000000000001
知识库: 新闻
来源: 财经日报 | 发布: 2026-08-18
实际命中片段:
央行今日宣布下调再贷款利率 0.25 个百分点。${'（中间部分已省略）'}此次调整自下月起生效。`

    const excerpt = excerptFor('E0123456789ab', [trace(pruned)])

    expect(excerpt?.body).toContain('（中间部分已省略）')
    expect(excerpt?.body).toBe(
      `央行今日宣布下调再贷款利率 0.25 个百分点。${'（中间部分已省略）'}此次调整自下月起生效。`,
    )
  })

  it('两个正文标记分开成常量，与后端拼法一一对应', () => {
    expect(EVIDENCE_BODY_MARKERS).toEqual(['实际命中片段:', '当前正文:'])
  })
})

describe('citationMarkerRegExp', () => {
  it('每次给一个新正则，遍历两遍不会因 lastIndex 漏匹配', () => {
    const ids = (value: string) =>
      [...value.matchAll(citationMarkerRegExp())].map((match) => match[1])

    expect(ids('甲 [[E0123456789ab]] 乙 [[E0b1c2d3e4f5a]]')).toEqual([
      'E0123456789ab',
      'E0b1c2d3e4f5a',
    ])
    expect(ids('甲 [[E0123456789ab]] 乙 [[E0b1c2d3e4f5a]]')).toEqual([
      'E0123456789ab',
      'E0b1c2d3e4f5a',
    ])
  })

  it('模式比后端剥离规则严：只认 E + 12 位小写十六进制', () => {
    const matches = (value: string) => [...value.matchAll(citationMarkerRegExp())].length

    expect(matches('[[E0123456789AB]]')).toBe(0)
    expect(matches('[[E0123456789]]')).toBe(0)
    expect(matches('[[E0123456789ab]]')).toBe(1)
  })
})
