import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AgentTurnCard from '../components/AgentTurnCard.vue'
import { createTurn, type AgentToolTrace, type AgentTurn } from '../model/conversation'
import type { DocumentEvidence } from '@/api/agent-evidence'
import { agentEvidence, agentScope } from '@/api/agent-chat.fixture'

function turn(overrides: Partial<AgentTurn> = {}): AgentTurn {
  return { ...createTurn('央行利率怎么走？'), ...overrides }
}

/* 监听器不写成 props 的字段，而是从 attrs 进：`onOpenEvidence` 这类键与 props 走同一条落点
 * （都进 vnode.props），emit 时照样被调到。 */
function mountCard(
  overrides: Partial<AgentTurn> = {},
  canRetry = true,
  listeners: Record<string, unknown> = {},
) {
  return mount(AgentTurnCard, {
    props: { turn: turn(overrides), canRetry },
    attrs: listeners,
  })
}

/* 工具正文的夹具是从后端真实输出抄来的字面量（拼法出处：
 * backend/src/agent_lab/agent/tools/search_documents.py 与 read_document.py）。
 * 自己另造一套格式只能证明切块自洽，证明不了它跟后端对得上。 */
const SEARCH_CONTENT = `本次检索范围：新闻

[[E0123456789ab]] 运行手册
document_id: 20000000-0000-4000-8000-000000000001
知识库: 新闻
来源: 无外部来源 | 发布: 发布时间未知
实际命中片段:
备份保留 7 天，清理任务每天凌晨两点执行。

[[E0b1c2d3e4f5a]] 存量房贷利率怎么走
document_id: 20000000-0000-4000-8000-000000000002
知识库: 新闻
来源: 财经日报 | 发布: 2026-08-18
实际命中片段:
多家银行表示，存量房贷利率将在下月同步调整。`

const READ_CONTENT = `[[E0aaaaaaaaaaa]] 标题: 备份规定
document_id: 20000000-0000-4000-8000-000000000003
知识库: 运维
来源: 备份规定.md | 发布: 2026-07-01 09:30
作者: 未署名
原文地址: 无外部地址

当前正文:
备份保留 14 天，冷备每天凌晨三点同步到异机。`

const secondEvidence: DocumentEvidence = {
  ...agentEvidence,
  citation_id: 'E0b1c2d3e4f5a',
  document_id: '20000000-0000-4000-8000-000000000002',
  title: '存量房贷利率怎么走',
  source_name: '财经日报',
  upload_filename: null,
  kind: 'match',
}

const readEvidence: DocumentEvidence = {
  ...agentEvidence,
  citation_id: 'E0aaaaaaaaaaa',
  document_id: '20000000-0000-4000-8000-000000000003',
  knowledge_base_name: '运维',
  knowledge_base_id: '10000000-0000-4000-8000-000000000002',
  title: '备份规定',
  source_name: '运行手册.md',
  kind: 'document',
}

function trace(content: string | null, overrides: Partial<AgentToolTrace> = {}): AgentToolTrace {
  return {
    id: 'trace-search',
    toolCallId: null,
    tool: 'search_documents',
    arguments: { query: '利率' },
    content,
    failed: false,
    ...overrides,
  }
}

describe('AgentTurnCard', () => {
  it('只有本次实际引用能打开证据，伪造编号没有已验证入口', async () => {
    const onOpenEvidence = vi.fn()
    const wrapper = mountCard(
      {
        answer: '保留 7 天。[[E0123456789ab]] 另一项 [[Effffffffffff]]',
        status: 'done',
        scope: agentScope,
        citations: [agentEvidence],
        invalidCitations: ['Effffffffffff'],
        traces: [trace(SEARCH_CONTENT)],
      },
      true,
      { onOpenEvidence },
    )
    const links = wrapper.findAll('.answer-body a')
    expect(links).toHaveLength(1)
    expect(links[0]!.text()).toBe('[1]')
    await links[0]!.trigger('click')
    /* 这枚小丸现在开的是面板，不再直接把用户丢进当前全文。 */
    expect(onOpenEvidence).not.toHaveBeenCalled()
    expect(wrapper.get('.evidence-panel').text()).toContain('运行手册')
    expect(wrapper.text()).toContain('部分引用未能对应')
  })

  it('截断的回答保留文字，但不加结局标签', () => {
    /* 被中断的那一次运行就是「停在哪里显示到哪里」：不做「回答未完成 / 已停止 / 以上内容不完整」
       这类提示（2026-09 去掉）。那两句本来也覆盖「达到处理上限」等其他截断情形，一并去掉。 */
    for (const status of ['incomplete'] as const) {
      const wrapper = mountCard({ answer: '回答到一半', status })

      expect(wrapper.text()).toContain('回答到一半')
      expect(wrapper.text()).not.toContain('回答未完成')
      expect(wrapper.text()).not.toContain('已停止')
      expect(wrapper.text()).not.toContain('以上内容不完整')
    }
  })

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
          tool: 'search_documents',
          arguments: { query: '利率' },
          content: '找到 2 篇。',
          failed: false,
        },
      ],
    })

    const html = wrapper.html()
    expect(html.indexOf('trace-block')).toBeLessThan(html.indexOf('answer-body'))
  })

  it('回答完成后提供复制键，点击写入剪贴板并就地反馈', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    try {
      const wrapper = mountCard({ answer: '答案正文。', status: 'done' })

      await wrapper.get('.ghost-action').trigger('click')

      expect(writeText).toHaveBeenCalledWith('答案正文。')
      expect(wrapper.get('.ghost-action').text()).toContain('已复制')
    } finally {
      Reflect.deleteProperty(navigator, 'clipboard')
    }
  })

  it('流式中的轮次不给悬停操作键', () => {
    const wrapper = mountCard({ answer: '写到一半', status: 'streaming' })

    expect(wrapper.find('.answer-actions').exists()).toBe(false)
  })

  describe('证据面板', () => {
    /* 「要不要改开当前全文」全看 open-evidence 这一个出口。这里盯的是监听器本身被调到、
       拿到什么参数；VTU 的 emitted() 在本仓库当前依赖组合下记不到事件（emit 照常触发，
       只是记录为空），因此不去读那份记录。 */
    function panelCard(overrides: Partial<AgentTurn> = {}, onOpenEvidence = vi.fn()) {
      return mountCard(
        {
          answer: '备份保留 7 天。[[E0123456789ab]]',
          status: 'done',
          citations: [agentEvidence],
          traces: [trace(SEARCH_CONTENT)],
          ...overrides,
        },
        true,
        { onOpenEvidence },
      )
    }

    /* 「点面板外」与跨轮收起都要事件冒泡到 document，而脱离文档的组件树冒不上去，
       所以这几条挂到 body 上，用完 unmount。 */
    function attachedCard(overrides: Partial<AgentTurn> = {}) {
      return mount(AgentTurnCard, {
        props: {
          turn: turn({
            answer: '备份保留 7 天。[[E0123456789ab]]',
            status: 'done',
            citations: [agentEvidence],
            traces: [trace(SEARCH_CONTENT)],
            ...overrides,
          }),
          canRetry: true,
        },
        attachTo: document.body,
      })
    }

    it('点引用列表里的一条，面板展开在该条下方，主体是当初那段命中片段', async () => {
      const onOpenEvidence = vi.fn()
      const wrapper = panelCard({}, onOpenEvidence)

      await wrapper.get('.citation-list > li > button').trigger('click')

      /* 位置：在被点中的那一条 li 之内，不是整份列表之后、也不是答案与列表之间。 */
      const item = wrapper.get('.citation-list li')
      const panel = item.get('.evidence-panel')
      expect(panel.text()).toContain('备份保留 7 天，清理任务每天凌晨两点执行。')
      expect(onOpenEvidence).not.toHaveBeenCalled()
    })

    it('头部显示这条引用的标题、知识库、来源与发布时间', async () => {
      const wrapper = panelCard()

      await wrapper.get('.citation-list > li > button').trigger('click')

      const text = wrapper.get('.evidence-panel').text()
      expect(text).toContain(agentEvidence.title)
      expect(text).toContain(agentEvidence.knowledge_base_name)
      /* 来源缺失时退到上传文件名；发布时间的缺值口径与阅读界面一致。 */
      expect(text).toContain('运行手册.md')
      expect(text).toContain('时间未提供')
    })

    it('来源与上传文件名都缺时头部写「无外部来源」', async () => {
      const wrapper = panelCard({
        citations: [{ ...agentEvidence, source_name: null, upload_filename: null }],
      })

      await wrapper.get('.citation-list > li > button').trigger('click')

      expect(wrapper.get('.evidence-panel').text()).toContain('无外部来源')
    })

    it('全文读取来源对上「当前正文」，不是命中片段也不是元数据行', async () => {
      const wrapper = panelCard({
        answer: '备份保留 14 天。[[E0aaaaaaaaaaa]]',
        citations: [readEvidence],
        traces: [trace(READ_CONTENT, { tool: 'read_document', id: 'trace-read' })],
      })

      await wrapper.get('.citation-list > li > button').trigger('click')

      const text = wrapper.get('.evidence-body').text()
      expect(text).toBe('备份保留 14 天，冷备每天凌晨三点同步到异机。')
    })

    it('一份工具正文里拼了多个块时，点第 2 篇拿到的是第 2 篇的正文', async () => {
      const wrapper = panelCard({ citations: [agentEvidence, secondEvidence] })

      await wrapper.findAll('.citation-list > li > button')[1]!.trigger('click')

      const text = wrapper.get('.evidence-panel').text()
      expect(text).toContain('多家银行表示，存量房贷利率将在下月同步调整。')
      expect(text).not.toContain('清理任务每天凌晨两点执行')
    })

    it('点另一条即切换，同一时刻只有一个面板，再点同一条即收起', async () => {
      const wrapper = panelCard({ citations: [agentEvidence, secondEvidence] })
      const buttons = () => wrapper.findAll('.citation-list > li > button')

      await buttons()[0]!.trigger('click')
      expect(wrapper.findAll('.evidence-panel')).toHaveLength(1)
      expect(buttons()[0]!.attributes('aria-expanded')).toBe('true')
      expect(buttons()[1]!.attributes('aria-expanded')).toBe('false')

      await buttons()[1]!.trigger('click')
      expect(wrapper.findAll('.evidence-panel')).toHaveLength(1)
      expect(wrapper.get('.evidence-panel').text()).toContain('存量房贷利率')
      expect(buttons()[1]!.attributes('aria-expanded')).toBe('true')

      await buttons()[1]!.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
      expect(buttons()[1]!.attributes('aria-expanded')).toBe('false')
    })

    it('触发按钮的 aria-controls 指向面板本身的 id', async () => {
      const wrapper = panelCard()

      await wrapper.get('.citation-list > li > button').trigger('click')

      const button = wrapper.get('.citation-list > li > button')
      expect(button.attributes('aria-controls')).toBe(wrapper.get('.evidence-panel').attributes('id'))
    })

    it('只有点面板底部的「查看当前全文」才打开全文', async () => {
      const onOpenEvidence = vi.fn()
      const wrapper = panelCard({}, onOpenEvidence)

      await wrapper.get('.citation-list > li > button').trigger('click')
      expect(onOpenEvidence).not.toHaveBeenCalled()

      const button = wrapper.get('.evidence-panel .evidence-fulltext')
      expect(button.text()).toBe('查看当前全文')
      await button.trigger('click')

      expect(onOpenEvidence).toHaveBeenCalledWith(agentEvidence, button.element)
    })

    it('点正文里的内联小丸，面板展开在同一轮的引用条目下方并被滚进视口', async () => {
      const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView')
      const wrapper = panelCard()

      await wrapper.get('.answer-body a.verified-citation').trigger('click')

      expect(wrapper.find('.citation-list li .evidence-panel').exists()).toBe(true)
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    })

    it('点面板外收起', async () => {
      const wrapper = panelCard()
      await wrapper.get('.citation-list > li > button').trigger('click')

      document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
    })

    it('按 Esc 收起，焦点回到触发元素', async () => {
      const wrapper = attachedCard()
      const button = wrapper.get('.citation-list > li > button')
      await button.trigger('click')

      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
      expect(document.activeElement).toBe(button.element)
      wrapper.unmount()
    })

    it('从正文小丸打开后按 Esc，焦点回到那枚小丸，可以直接再展开一次', async () => {
      const wrapper = attachedCard()
      const pill = wrapper.get('.answer-body a.verified-citation')

      await pill.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)

      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await wrapper.vm.$nextTick()

      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
      /* 焦点真的落回那枚小丸：它带着 href（不是被剥掉 href 的伪引用），因此是可聚焦元素，
         也就进得了 Tab 序、回车时浏览器会派发 click——下面直接派发的就是同一次 click。 */
      expect(document.activeElement).toBe(pill.element)
      expect((pill.element as HTMLElement).tabIndex).toBe(0)

      await pill.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)
      wrapper.unmount()
    })

    it('收起后面板从页面结构里消失，不是被样式藏起来，再点同一条可以重新展开', async () => {
      const wrapper = panelCard()
      const button = wrapper.get('.citation-list > li > button')

      await button.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)

      await button.trigger('click')

      /* 整份 HTML 里连面板的 id 都不该剩下：v-show 或 display:none 会留下节点。 */
      expect(wrapper.html()).not.toContain('evidence-panel')

      await button.trigger('click')
      expect(wrapper.find('.citation-list li .evidence-panel').exists()).toBe(true)
    })

    it('点面板里面不收起，点本轮别处收起', async () => {
      const wrapper = attachedCard()
      await wrapper.get('.citation-list > li > button').trigger('click')

      /* 面板里选一段正文、或去点底部的查看全文，都不算点外面。 */
      await wrapper.get('.evidence-body').trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)

      await wrapper.get('.question-text').trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
      wrapper.unmount()
    })

    it('列表条目与正文小丸走同一套判定：任一处再点同一条即收起', async () => {
      const wrapper = panelCard()
      const button = wrapper.get('.citation-list > li > button')
      const pill = wrapper.get('.answer-body a.verified-citation')

      await button.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)

      await pill.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(false)

      await pill.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(true)

      await button.trigger('click')
      expect(wrapper.find('.evidence-panel').exists()).toBe(false)
    })

    it('正文小丸展开的是同一轮里对应那一条的面板；只有从它打开时才滚进视口', async () => {
      const scrollIntoView = vi.spyOn(Element.prototype, 'scrollIntoView')
      const wrapper = panelCard({
        answer: '备份保留 7 天。[[E0123456789ab]] 房贷 [[E0b1c2d3e4f5a]]',
        citations: [agentEvidence, secondEvidence],
      })

      /* 列表里点的那条本来就在眼前，不该为了它动滚动位置。 */
      await wrapper.get('.citation-list > li > button').trigger('click')
      expect(scrollIntoView).not.toHaveBeenCalled()
      await wrapper.get('.citation-list > li > button').trigger('click')

      const pills = wrapper.findAll('.answer-body a.verified-citation')
      expect(pills).toHaveLength(2)
      await pills[1]!.trigger('click')

      const items = wrapper.findAll('.citation-list > li')
      expect(items[0]!.find('.evidence-panel').exists()).toBe(false)
      expect(items[1]!.get('.evidence-panel').text()).toContain('存量房贷利率')
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    })

    it('aria 只在展开的那一条上指向真实存在的面板，切走之后让位', async () => {
      const wrapper = panelCard({ citations: [agentEvidence, secondEvidence] })
      const buttons = () => wrapper.findAll('.citation-list > li > button')

      /* 收起状态下没有面板可指，就不挂一个指向不存在的 id 的 aria-controls。 */
      expect(buttons()[0]!.attributes('aria-expanded')).toBe('false')
      expect(buttons()[0]!.attributes('aria-controls')).toBeUndefined()

      await buttons()[0]!.trigger('click')
      expect(buttons()[0]!.attributes('aria-controls')).toBe(
        wrapper.get('.evidence-panel').attributes('id'),
      )
      expect(buttons()[1]!.attributes('aria-controls')).toBeUndefined()

      await buttons()[1]!.trigger('click')
      expect(buttons()[0]!.attributes('aria-expanded')).toBe('false')
      expect(buttons()[0]!.attributes('aria-controls')).toBeUndefined()
      expect(buttons()[1]!.attributes('aria-controls')).toBe(
        wrapper.get('.evidence-panel').attributes('id'),
      )
    })

    it('另一轮里点引用也收起本轮的面板，同时只有一个面板展开', async () => {
      const first = attachedCard()
      const second = attachedCard()

      await first.get('.citation-list > li > button').trigger('click')
      expect(first.find('.evidence-panel').exists()).toBe(true)

      await second.get('.answer-body a.verified-citation').trigger('click')

      /* 别的轮次的引用确实在这个面板之外：不能因为它是「引用」就把它当成面板内部。 */
      expect(first.find('.evidence-panel').exists()).toBe(false)
      expect(second.find('.evidence-panel').exists()).toBe(true)
      first.unmount()
      second.unmount()
    })

    it('被历史清理过的正文照原样显示占位文字，不静默跳去打开全文', async () => {
      const pruned = SEARCH_CONTENT.replace(
        '清理任务每天凌晨两点执行。',
        '清理任务每天凌晨两点执行。（中间部分已省略）',
      )
      const onOpenEvidence = vi.fn()
      const wrapper = panelCard({ traces: [trace(pruned)] }, onOpenEvidence)

      await wrapper.get('.citation-list > li > button').trigger('click')

      expect(wrapper.get('.evidence-body').text()).toContain('（中间部分已省略）')
      expect(onOpenEvidence).not.toHaveBeenCalled()
    })

    describe('三条降级路径都直接打开当前全文', () => {
      async function expectFallbackToFulltext(overrides: Partial<AgentTurn>) {
        const onOpenEvidence = vi.fn()
        const wrapper = panelCard(overrides, onOpenEvidence)

        await wrapper.get('.citation-list > li > button').trigger('click')

        /* 不展开空白面板、不加解释性文案：行为与面板落地前一致。 */
        expect(wrapper.find('.evidence-panel').exists()).toBe(false)
        expect(wrapper.text()).not.toContain('无法显示')
        expect(onOpenEvidence).toHaveBeenCalledTimes(1)
        expect(onOpenEvidence.mock.calls[0]?.[0]).toEqual(agentEvidence)
      }

      it('这一轮找不到含该标记的 Tool 结果', async () => {
        await expectFallbackToFulltext({ traces: [trace('这次调用没有留下结果记录。')] })
        await expectFallbackToFulltext({ traces: [] })
      })

      it('找得到标记但切不出该引用对应的块', async () => {
        const content = `说明见 [[E0123456789ab]] 这一行\n实际命中片段:\n备份保留 7 天。`
        await expectFallbackToFulltext({ traces: [trace(content)] })
      })

      it('块里取不到主体', async () => {
        const content = `[[E0123456789ab]] 运行手册\n知识库: 新闻\n来源: 无外部来源\n实际命中片段:\n  `
        await expectFallbackToFulltext({ traces: [trace(content)] })
      })
    })

    it('全文读取的整篇正文在面板内限高滚动，不撑长对话流', async () => {
      const wrapper = panelCard({
        answer: '备份保留 14 天。[[E0aaaaaaaaaaa]]',
        citations: [readEvidence],
        traces: [trace(READ_CONTENT, { tool: 'read_document', id: 'trace-read' })],
      })

      await wrapper.get('.citation-list > li > button').trigger('click')

      /* 具体像素不进验收（样式值改不动行为），这里钉住的是「有一个限高容器在承载正文」：
         正文直接从面板根节点铺出来，就没有任何地方能内部滚动。 */
      expect(wrapper.find('.evidence-body').exists()).toBe(true)
      expect(wrapper.get('.evidence-body').text()).toContain('备份保留 14 天')
    })
  })
})
