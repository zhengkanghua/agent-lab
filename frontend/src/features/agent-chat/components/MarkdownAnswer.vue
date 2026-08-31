<script setup lang="ts">
import { VueMarkdown, type CustomAttrs } from '@crazydos/vue-markdown'
import remarkGfm from 'remark-gfm'

/* 只渲染 Agent 答案正文的 Markdown。
 *
 * 为什么只给答案正文：工具入参、工具返回内容、用户提问三处继续走纯文本插值。
 * 前两者是外部抓来的新闻原文与模型的原始输出，把它们当 Markdown 解析等于让上游内容
 * 决定本页的排版；用户提问按 Markdown 渲染更怪——他打的星号就是星号。
 *
 * 三个 prop 配置都是实测定下来的，不是抄默认值：
 *
 * 1. sanitize 必须为 true。这个包默认 false，且默认 sanitizeOptions 是
 *    `{ allowDangerousHtml: true }`。默认配置下实测：裸 HTML（`<script>`、
 *    `<img onerror>`）确实会被转义成文本——因为没装 rehype-raw，raw 节点进不了 hast，
 *    这条安全。但 Markdown 链接语法里的 javascript: URL 会原样渲染成活的 href：
 *    `[点这里](javascript:...)` → `<a href="javascript:...">`。答案正文是模型输出，
 *    模型可以被检索到的新闻内容影响，所以这是真实注入面。开 sanitize 后同一输入
 *    渲染成 `<a>点这里</a>`，href 被摘掉。
 *
 * 2. 不引入 rehype-raw。装上它 raw 节点就会变成真 HTML，第 1 条的转义保护随之消失。
 *
 * 3. remark-gfm 是为表格装的。答案里列来源常常是表格，不装的话
 *    `| 来源 | 日期 |` 会渲染成一行竖线字面量。顺带拿到删除线、自动链接、任务列表。
 *
 * 性能：VueMarkdown 是同步的，流式期间每个 token 都会整段重新 parse。答案量级是几千字，
 * unified 单次 parse 在毫秒级，实测没有卡顿，所以没上 VueMarkdownAsync——那个会引入
 * 异步组件的挂起态，代价比这里省下的解析时间大。
 */

defineProps<{
  markdown: string
  /** 流式中在末尾显示光标块。落定后撤掉。 */
  streaming?: boolean
}>()

/* 外链开新标签页，站内链接不动。
 *
 * 用函数形式而不是对象形式：对象形式会把 target="_blank" 盖到所有 a 上，
 * 包括模型写出的站内相对路径（`/agent`），那种应该在当前页跳转。
 * rel 两个值都要：noopener 断掉 window.opener 提权，noreferrer 不漏当前地址。
 */
const linkAttrs: CustomAttrs = {
  a: (node) => {
    const href = node.properties?.href
    if (typeof href !== 'string') return {}
    // sanitize 之后 href 只可能是安全协议或相对路径，这里只需判断是否同源。
    // 相对路径解析后 origin 与当前页相同，自然落到 false。
    let external = false
    try {
      external = new URL(href, window.location.href).origin !== window.location.origin
    } catch {
      external = false
    }
    return external ? { target: '_blank', rel: 'noopener noreferrer' } : {}
  },
}
</script>

<template>
  <VueMarkdown
    class="markdown-answer"
    :class="{ 'is-streaming': streaming }"
    :markdown="markdown"
    sanitize
    :remark-plugins="[remarkGfm]"
    :custom-attrs="linkAttrs"
  />
</template>

<style scoped>
/* 正文块的样式全部要走 :deep()：这些节点由 VueMarkdown 创建，拿不到本组件的作用域属性。
   选择器都收在 .markdown-answer 下，不会漏到别处。 */

.markdown-answer {
  color: var(--text-primary);
  font-size: 0.92rem;
  line-height: 1.75;
  overflow-wrap: anywhere;
}

/* 块间距用「相邻兄弟加上边距」而不是给每块加下边距：后者会在正文末尾多留一段空白，
   答案卡的下沿就会比上沿宽。 */
.markdown-answer :deep(* + *) {
  margin-top: 0.85em;
}

.markdown-answer :deep(h1),
.markdown-answer :deep(h2),
.markdown-answer :deep(h3),
.markdown-answer :deep(h4) {
  color: var(--text-primary);
  font-weight: 760;
  line-height: 1.35;
}

/* 答案正文里的标题不该比页面 h1 还大：模型很爱用 `#`，照浏览器默认渲染会盖过页面层级。
   四级压到一个窄区间，靠字重和间距区分，不靠字号。 */
.markdown-answer :deep(h1) {
  font-size: 1.12rem;
}

.markdown-answer :deep(h2) {
  font-size: 1.04rem;
}

.markdown-answer :deep(h3),
.markdown-answer :deep(h4) {
  font-size: 0.96rem;
}

.markdown-answer :deep(* + h1),
.markdown-answer :deep(* + h2),
.markdown-answer :deep(* + h3),
.markdown-answer :deep(* + h4) {
  margin-top: 1.3em;
}

.markdown-answer :deep(ul),
.markdown-answer :deep(ol) {
  padding-left: 1.35em;
}

.markdown-answer :deep(li + li) {
  margin-top: 0.3em;
}

/* 列表项内的段落不再加块间距：remark 会给「松散列表」的每项包一层 p，
   不压掉的话每个 li 里都会多出 0.85em。 */
.markdown-answer :deep(li > p) {
  margin-top: 0;
}

.markdown-answer :deep(li > p + p) {
  margin-top: 0.5em;
}

.markdown-answer :deep(strong) {
  color: var(--text-primary);
  font-weight: 720;
}

.markdown-answer :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-answer :deep(a:hover) {
  color: var(--accent-hover);
}

/* 行内码与代码块共用等宽字体，但底色不同：行内的要在正文流里可辨认又不打断阅读，
   块级的要像一个独立区域。 */
.markdown-answer :deep(code) {
  padding: 0.12em 0.34em;
  border-radius: 4px;
  color: var(--text-secondary);
  background: var(--surface-sunken);
  font-family: var(--mono-font);
  font-size: 0.86em;
}

.markdown-answer :deep(pre) {
  max-height: 420px;
  overflow: auto;
  padding: 12px 13px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--surface-base);
}

/* pre 里的 code 要退掉行内码那套底色与内边距，否则代码块里会套一层灰底。 */
.markdown-answer :deep(pre code) {
  padding: 0;
  border-radius: 0;
  background: none;
  font-size: 0.78rem;
  line-height: 1.6;
  white-space: pre;
}

.markdown-answer :deep(blockquote) {
  padding: 2px 0 2px 13px;
  border-left: 2px solid var(--accent-soft);
  color: var(--text-secondary);
}

.markdown-answer :deep(table) {
  display: block;
  width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  font-size: 0.82rem;
}

.markdown-answer :deep(th),
.markdown-answer :deep(td) {
  padding: 7px 11px;
  border: 1px solid var(--border-subtle);
  text-align: left;
  vertical-align: top;
}

.markdown-answer :deep(th) {
  color: var(--text-secondary);
  background: var(--surface-base);
  font-weight: 720;
  white-space: nowrap;
}

.markdown-answer :deep(hr) {
  border: 0;
  border-top: 1px solid var(--border-subtle);
}

.markdown-answer :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-sm);
}

/* 流式光标：只贴在最后一个直接子块的末尾。
   `:deep(> :last-child)` 编译成「本组件根节点 > 最后一个子元素」，
   所以不会漏到嵌套结构里去给每层都加一个光标。
   流式期间正文通常停在段落中间，落点就是那个 p 的行尾。 */
.markdown-answer.is-streaming :deep(> :last-child)::after {
  content: '';
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 3px;
  background: var(--accent);
  vertical-align: text-bottom;
  animation: caret-blink 1s step-end infinite;
}

@keyframes caret-blink {
  50% {
    opacity: 0;
  }
}
</style>
