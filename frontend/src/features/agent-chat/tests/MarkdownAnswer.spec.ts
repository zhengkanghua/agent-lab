import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MarkdownAnswer from '../components/MarkdownAnswer.vue'

/* 这份 spec 的重点是「配置没被改掉」而不是「Markdown 能渲染」。
   sanitize 和不装 rehype-raw 这两条各自堵住一个注入面，两条都不会在界面上显形，
   只有测试能盯住。剩下的结构断言用来防止哪天有人把 remark-gfm 摘掉。 */

function render(markdown: string, streaming = false) {
  return mount(MarkdownAnswer, { props: { markdown, streaming } })
}

describe('MarkdownAnswer 安全配置', () => {
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

describe('MarkdownAnswer 流式光标', () => {
  it('streaming 为真时挂上 is-streaming', () => {
    expect(render('写到一半', true).classes()).toContain('is-streaming')
  })

  it('落定后撤掉 is-streaming', async () => {
    const wrapper = render('写完了', true)

    await wrapper.setProps({ streaming: false })

    expect(wrapper.classes()).not.toContain('is-streaming')
  })

  it('默认不带 is-streaming', () => {
    expect(render('已完成').classes()).not.toContain('is-streaming')
  })
})
