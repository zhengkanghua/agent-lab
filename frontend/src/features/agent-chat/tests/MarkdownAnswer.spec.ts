import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import MarkdownAnswer from '@/shared/ui/SafeMarkdown.vue'

/* 这份 spec 的重点是「配置没被改掉」而不是「Markdown 能渲染」。
   sanitize 和不装 rehype-raw 这两条各自堵住一个注入面，两条都不会在界面上显形，
   只有测试能盯住。剩下的结构断言用来防止哪天有人把 remark-gfm 摘掉。 */

function render(markdown: string, streaming = false) {
  return mount(MarkdownAnswer, { props: { markdown, streaming } })
}

describe('MarkdownAnswer 安全配置', () => {
  it('代码中的引用不变成入口，文件没有服务端引用白名单时也不生成入口', () => {
    const id = 'E0123456789ab'
    const wrapper = mount(MarkdownAnswer, {
      props: { markdown: `代码 \`[[${id}]]\`\n\n实际引用 [[${id}]]`, citationIds: [id] },
    })
    expect(wrapper.get('code').text()).toBe(`[[${id}]]`)
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(render(`[[${id}]]`).find('a').exists()).toBe(false)
    expect(render('[伪造](#evidence-Effffffffffff)').get('a').attributes('href')).toBeUndefined()
  })

  it('已有链接即使用了真实引用 ID，也不能伪装成应用的引用入口', async () => {
    const id = 'E0123456789ab'
    const wrapper = mount(MarkdownAnswer, {
      props: {
        markdown: `实际引用 [[${id}]]\n\n[伪造](#evidence-${id}) [伪造定义][fake]\n\n[fake]: #evidence-${id}`,
        citationIds: [id],
      },
    })
    const links = wrapper.findAll('a')
    expect(links).toHaveLength(3)
    await links[0]!.trigger('click')
    expect(wrapper.emitted('citation')).toHaveLength(1)
    for (const link of links.slice(1)) {
      expect(link.attributes('href')).toBeUndefined()
      await link.trigger('click')
    }
    expect(wrapper.emitted('citation')).toHaveLength(1)
  })

  it('裸 HTML 被转义成文本，不进 DOM', () => {
    const wrapper = render('<img src=x onerror="window.__pwned=1">')

    // 没装 rehype-raw，raw 节点进不了 hast；再加上 sanitize，这里连元素都不该出现。
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('onerror')
  })

  it('script 标签不进 DOM', () => {
    const wrapper = render('<script>window.__pwned=1</script>')

    expect(wrapper.find('script').exists()).toBe(false)
  })

  it('Markdown 链接里的 javascript: 协议被摘掉 href', () => {
    /* 这条是开 sanitize 的直接理由：这个包默认 sanitize=false，那时同样的输入会渲染出
       `<a href="javascript:...">`——裸 HTML 那条保护拦不住它，因为它走的是链接语法。 */
    const wrapper = render('[点这里](javascript:window.__pwned=1)')
    const link = wrapper.get('a')

    expect(link.text()).toBe('点这里')
    expect(link.attributes('href')).toBeUndefined()
  })

  it('data: 协议的图片被摘掉 src', () => {
    const wrapper = render('![图](data:text/html;base64,PHNjcmlwdD48L3NjcmlwdD4=)')

    expect(wrapper.find('img').attributes('src')).toBeUndefined()
  })

  it('阅读 Markdown 不自动下载外部图片', () => {
    const image = render('![结构图](https://example.com/private-image.png)').get('img')
    expect(image.attributes('src')).toBeUndefined()
    expect(image.attributes('alt')).toContain('结构图')
  })

  it('正常的 http 链接与相对路径保留', () => {
    const wrapper = render('[外](https://example.com/a?b=1) 和 [内](/agent)')
    const links = wrapper.findAll('a')

    expect(links[0]?.attributes('href')).toBe('https://example.com/a?b=1')
    expect(links[1]?.attributes('href')).toBe('/agent')
  })
})

describe('MarkdownAnswer 外链属性', () => {
  it('外链开新标签页并带上 noopener noreferrer', () => {
    const link = render('[外](https://example.com)').get('a')

    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
  })

  it('站内相对链接不开新标签页', () => {
    // 对象形式的 customAttrs 会把 target 盖到所有 a 上，这条钉住函数形式的判断。
    const link = render('[内](/agent)').get('a')

    expect(link.attributes('target')).toBeUndefined()
    expect(link.attributes('rel')).toBeUndefined()
  })

  it('href 不是字符串时不炸', () => {
    // 没有 href 的自动链接语法之外，锚点也走同一条分支。
    const wrapper = render('[锚](#section)')

    expect(wrapper.get('a').attributes('target')).toBeUndefined()
  })
})

describe('MarkdownAnswer 结构渲染', () => {
  it('表格按 GFM 解析，不是一行竖线字面量', () => {
    // remark-gfm 是为这条装的：答案里列来源常用表格。
    const wrapper = render('| 来源 | 日期 |\n| --- | --- |\n| 甲 | 3日 |')

    expect(wrapper.find('table').exists()).toBe(true)
    expect(wrapper.findAll('th').map((th) => th.text())).toEqual(['来源', '日期'])
    expect(wrapper.findAll('td').map((td) => td.text())).toEqual(['甲', '3日'])
  })

  it('删除线与自动链接按 GFM 解析', () => {
    const wrapper = render('~~删~~ https://auto.example.com')

    expect(wrapper.find('del').exists()).toBe(true)
    expect(wrapper.get('a').attributes('href')).toBe('https://auto.example.com')
  })

  it('标题、列表、代码块、引用各自成块', () => {
    const wrapper = render('# 标\n\n- 一\n- 二\n\n```py\nprint(1)\n```\n\n> 引')

    expect(wrapper.get('h1').text()).toBe('标')
    expect(wrapper.findAll('li').map((li) => li.text())).toEqual(['一', '二'])
    expect(wrapper.get('pre code').text()).toContain('print(1)')
    expect(wrapper.get('blockquote').text()).toBe('引')
  })

  it('行内码与代码块都渲染成 code', () => {
    const wrapper = render('行内 `x` 与\n\n```\nblock\n```')

    expect(wrapper.findAll('code')).toHaveLength(2)
  })

  it('纯文本答案渲染成段落', () => {
    expect(render('就是一句话。').get('p').text()).toBe('就是一句话。')
  })

  it('空字符串不炸也不留空段落', () => {
    const wrapper = render('')

    expect(wrapper.find('p').exists()).toBe(false)
  })
})

describe('MarkdownAnswer 引用悬停预览', () => {
  const id = 'E0123456789ab'
  const markdown = `备份保留 7 天 [[${id}]]`
  const summaries = { [id]: '技术资料 · 运行手册' }

  /* 卡片由 Teleport 挂到 body，不在组件的模板里，所以只能从 document 上找。
     它原来是正文里的 CSS 伪元素，会被表格 / 代码块的 overflow 裁掉一半——
     挂在 body 上正是为了那条。 */
  function previewCard(): HTMLElement | null {
    return document.body.querySelector('.citation-preview')
  }

  function mountWithSummary(props: Record<string, unknown> = {}) {
    return mount(MarkdownAnswer, {
      props: { markdown, citationIds: [id], citationSummaries: summaries, ...props },
    })
  }

  /* 悬停与聚焦走根的委托，所以事件要从小丸上冒泡上去——直接在根上派发测不到
     「找到是哪一枚小丸」。 */
  async function fire(wrapper: ReturnType<typeof mountWithSummary>, type: string): Promise<void> {
    wrapper.get('a.verified-citation').element.dispatchEvent(new Event(type, { bubbles: true }))
    await nextTick()
  }

  afterEach(() => {
    document.body.replaceChildren()
  })

  it('悬停引用小丸浮出摘要，移开就收起', async () => {
    const wrapper = mountWithSummary()

    expect(previewCard()).toBeNull()

    await fire(wrapper, 'mouseover')
    expect(previewCard()?.textContent?.trim()).toBe('技术资料 · 运行手册')

    await fire(wrapper, 'mouseout')
    expect(previewCard()).toBeNull()
  })

  it('键盘聚焦同样浮出，卡片挂在 body 上而不留在正文里', async () => {
    const wrapper = mountWithSummary()

    await fire(wrapper, 'focusin')
    expect(previewCard()).not.toBeNull()
    expect(previewCard()!.parentElement).toBe(document.body)
    // 留在正文里就会被 table / pre 的 overflow 裁掉。
    expect(wrapper.html()).not.toContain('citation-preview')

    await fire(wrapper, 'focusout')
    expect(previewCard()).toBeNull()
  })

  it('没有摘要时不浮出（普通文件阅读）', async () => {
    const wrapper = mount(MarkdownAnswer, { props: { markdown, citationIds: [id] } })

    await fire(wrapper, 'mouseover')

    expect(previewCard()).toBeNull()
  })

  it('点击仍然走 citation 事件，不受预览影响', async () => {
    const wrapper = mountWithSummary()

    await wrapper.get('a.verified-citation').trigger('click')

    expect(wrapper.emitted('citation')).toHaveLength(1)
  })

  it('父级传下来的 class 落在正文根节点上', () => {
    // 组件有两个根节点（正文 + 悬停卡的 Teleport），不显式转发就会静默丢掉。
    const wrapper = mount(MarkdownAnswer, {
      props: { markdown: '一句话' },
      attrs: { class: 'answer-body' },
    })

    expect(wrapper.get('.markdown-answer').classes()).toContain('answer-body')
  })
})

describe('MarkdownAnswer 流式光标', () => {
  /* 组件有两个根节点（正文 + 悬停卡的 Teleport），wrapper.classes() 取不到根上的类，
     要从正文根节点上看。 */
  it('streaming 为真时挂上 is-streaming', () => {
    expect(render('写到一半', true).get('.markdown-answer').classes()).toContain('is-streaming')
  })

  it('落定后撤掉 is-streaming', async () => {
    const wrapper = render('写完了', true)

    await wrapper.setProps({ streaming: false })

    expect(wrapper.get('.markdown-answer').classes()).not.toContain('is-streaming')
  })

  it('默认不带 is-streaming', () => {
    expect(render('已完成').get('.markdown-answer').classes()).not.toContain('is-streaming')
  })
})
