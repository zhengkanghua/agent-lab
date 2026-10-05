<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { VueMarkdown, type CustomAttrs } from '@crazydos/vue-markdown'
import remarkGfm from 'remark-gfm'
import { useFloatingPosition } from '@/shared/composables/useFloatingPosition'

/* Agent 答案与上传 Markdown 文档共用的安全渲染。
 *
 * 答案和明确为 Markdown 格式的文档需要保留结构；工具入参、工具返回内容、用户提问
 * 继续走纯文本插值，避免把轨迹中的原始输出和用户输入当成排版指令。
 *
 * 三个 prop 配置都是实测定下来的，不是抄默认值：
 *
 * 1. sanitize 必须为 true。这个包默认 false，且默认 sanitizeOptions 是
 *    `{ allowDangerousHtml: true }`。默认配置下实测：裸 HTML（`<script>`、
 *    `<img onerror>`）确实会被转义成文本——因为没装 rehype-raw，raw 节点进不了 hast，
 *    这条安全。但 Markdown 链接语法里的 javascript: URL 会原样渲染成活的 href：
 *    `[点这里](javascript:...)` → `<a href="javascript:...">`。答案正文是模型输出，
 *    模型可以被检索到的文档内容影响，上传文档也来自外部，所以这是真实注入面。开 sanitize 后同一输入
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

/* 本组件有两个根节点（渲染器 + 悬停卡的 Teleport），Vue 不会自动把父级传下来的
   class 挂到哪一个上——AgentTurnCard 就传了 `class="answer-body"`，不管它就静默丢掉。
   所以关掉自动继承，在渲染器那个根上显式 v-bind="$attrs"。 */
defineOptions({ inheritAttrs: false })

const props = defineProps<{
  markdown: string
  /** 流式中在末尾显示光标块。落定后撤掉。 */
  streaming?: boolean
  /** 只由服务端已核验的本次引用提供；普通文件阅读不传。 */
  citationIds?: string[]
  /** 引用 id → hover 摘要（来源 · 标题）。给了它，引用小丸悬停时浮出摘要卡。 */
  citationSummaries?: Readonly<Record<string, string>>
}>()

const emit = defineEmits<{ citation: [id: string, trigger: HTMLElement] }>()
const citationNumbers = computed(
  () => new Map((props.citationIds ?? []).map((id, index) => [id, index + 1])),
)

/* 引用悬停卡：一块跟着鼠标所在那枚小丸走的浮层。
 *
 * 它原来是纯 CSS 的 `::after`（`content: attr(data-summary)` + 绝对定位）。那个写法不用
 * JS、流式期间也不用管状态，但有一个修不掉的毛病：绝对定位的祖先链里只要有 `overflow`，
 * 它就会被裁。答案里恰恰有——`table` 是 `display: block; overflow-x: auto`（横竖都被裁），
 * 而模型很爱用表格列来源，引用正好落在单元格里；`pre` 同理。表现就是悬停时卡片被切掉一半，
 * 和输入坞那两块浮层是同一类问题。
 *
 * 现在改成一块 Teleport 到 body 的固定定位浮层，落点走与下拉面板同一套几何
 * （useFloatingPosition）：朝上、以小丸为中线，放不下才翻到下方或夹进视口。
 * 代价是有了 JS 状态，所以下面还有两处收尾：锚点离开文档要收卡片（流式期间
 * 整棵正文树每来一个 token 就重渲染一次，被悬停的那枚小丸随时可能被换掉）。
 *
 * 悬停与聚焦的判定走**事件委托**，挂在渲染器根节点上，而不是逐个小丸挂监听：
 *   1. 这个渲染器只把属性转发到元素上，事件属性并不保证转发——2026-10 实测，
 *      同一次返回里 onClick 生效、onMouseEnter 收不到（它自己文档也只演示 onClick），
 *      逐个小丸挂事件是在赌一个没有承诺的行为；
 *   2. 流式期间整棵正文树每个 token 重渲染一次，委托只有一个监听，
 *      不随小丸的增减而反复挂载。
 * 委托读的是元素上的 data-summary（属性转发是可靠的，title / class 都落在上面）。
 */
const previewSummary = ref<string | null>(null)
const previewAnchor = ref<HTMLElement | null>(null)
const previewPanel = ref<HTMLElement | null>(null)

const { position, positioned } = useFloatingPosition({
  open: () => previewSummary.value !== null,
  anchorRef: previewAnchor,
  panelRef: previewPanel,
  gap: 8,
  prefer: 'above',
  align: 'center',
})

/** 从事件目标找回承载摘要的那枚小丸；不在小丸上（或小丸没有摘要）就返回 null。 */
function citationAnchor(target: EventTarget | null): HTMLElement | null {
  if (!(target instanceof HTMLElement)) return null
  const anchor = target.closest<HTMLElement>('.verified-citation')
  return anchor?.dataset.summary ? anchor : null
}

function showPreview(anchor: HTMLElement): void {
  previewSummary.value = anchor.dataset.summary ?? null
  previewAnchor.value = anchor
}

function hidePreview(): void {
  previewSummary.value = null
  previewAnchor.value = null
}

function onPointerOver(event: MouseEvent): void {
  const anchor = citationAnchor(event.target)
  if (anchor) showPreview(anchor)
}

function onPointerOut(event: MouseEvent): void {
  if (citationAnchor(event.target)) hidePreview()
}

/* focusin / focusout 而不是 focus / blur：后两个不冒泡，委托收不到。 */
function onFocusIn(event: FocusEvent): void {
  const anchor = citationAnchor(event.target)
  if (anchor) showPreview(anchor)
}

function onFocusOut(event: FocusEvent): void {
  if (citationAnchor(event.target)) hidePreview()
}

/* 流式期间正文整棵重渲染，被悬停的那枚小丸可能已经被换掉。元素被移除时浏览器
   不一定会派发 mouseleave，卡片就会停在一个指向已消失元素的坐标上（那种元素的
   rect 全是 0，夹取之后它就贴在视口角上）。所以每次正文变化都核对一次锚点。 */
watch(
  () => props.markdown,
  () => {
    if (previewAnchor.value && !previewAnchor.value.isConnected) hidePreview()
  },
)

const previewStyle = computed(() => ({
  top: `${position.value.top}px`,
  left: `${position.value.left}px`,
}))

// 只转换 Markdown 解析后的文本节点，代码与已有链接保持原样；不自行解析 Markdown。
interface MarkdownNode {
  type: string
  value?: string
  url?: string
  children?: MarkdownNode[]
}

function remarkCitations() {
  return function transform(node: MarkdownNode): void {
    if (
      (node.type === 'link' || node.type === 'definition') &&
      node.url?.startsWith('#evidence-')
    ) {
      // 原有链接不拥有引用身份；清空保留前缀后的 ID，让下游摘除 href。
      node.url = '#evidence-'
    }
    if (!node.children || node.type === 'link' || node.type === 'linkReference') return
    node.children = node.children.flatMap((child) => {
      if (child.type !== 'text' || !child.value) {
        transform(child)
        return [child]
      }
      const parts: MarkdownNode[] = []
      let start = 0
      for (const match of child.value.matchAll(/\[\[(E[0-9a-f]{12})\]\]/g)) {
        const id = match[1]!
        const number = citationNumbers.value.get(id)
        if (number === undefined) continue
        parts.push({ type: 'text', value: child.value.slice(start, match.index) })
        parts.push({
          type: 'link',
          url: `#evidence-${id}`,
          children: [{ type: 'text', value: `[${number}]` }],
        })
        start = match.index + match[0].length
      }
      parts.push({ type: 'text', value: child.value.slice(start) })
      return parts
    })
  }
}

const remarkPlugins = computed(() =>
  props.citationIds?.length ? [remarkGfm, remarkCitations] : [remarkGfm],
)

/* 外链开新标签页，站内链接不动。
 *
 * 用函数形式而不是对象形式：对象形式会把 target="_blank" 盖到所有 a 上，
 * 包括模型写出的站内相对路径（`/agent`），那种应该在当前页跳转。
 * rel 两个值都要：noopener 断掉 window.opener 提权，noreferrer 不漏当前地址。
 */
const linkAttrs: CustomAttrs = {
  // 图片仅保留说明文字，不因阅读文档或回答而自动访问外部地址。
  img: (node) => ({
    src: undefined,
    srcset: undefined,
    alt: `${node.properties?.alt || '图片'}（未加载）`,
  }),
  a: (node) => {
    const href = node.properties?.href
    if (typeof href !== 'string') return {}
    if (href.startsWith('#evidence-')) {
      const id = href.slice('#evidence-'.length)
      const number = citationNumbers.value.get(id)
      if (number === undefined) return { href: undefined }
      const summary = props.citationSummaries?.[id]
      return {
        class: 'verified-citation',
        title: `查看引用 ${number}`,
        /* 摘要经 data-* 落给根的委托监听（见文件上方 onPointerOver 那段说明）；
           attr() 只吃纯文本，无注入面。 */
        'data-summary': summary,
        onClick: (event: MouseEvent) => {
          event.preventDefault()
          emit('citation', id, event.currentTarget as HTMLElement)
        },
      }
    }
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
    v-bind="$attrs"
    class="markdown-answer"
    :class="{ 'is-streaming': streaming }"
    :markdown="markdown"
    sanitize
    :remark-plugins="remarkPlugins"
    :custom-attrs="linkAttrs"
    @mouseover="onPointerOver"
    @mouseout="onPointerOut"
    @focusin="onFocusIn"
    @focusout="onFocusOut"
  />

  <!-- 悬停卡挂到 body：留在正文里会被 table / pre 的 overflow 裁掉，正是它要修的那个毛病。
       aria-hidden：卡片内容与点击后打开的阅读层重复，而原来的 CSS 伪元素内容本来就读屏不可见，
       屏开等于凭空多播一遍。 -->
  <Teleport to="body">
    <div
      v-if="previewSummary"
      ref="previewPanel"
      class="citation-preview"
      :class="{ 'is-positioned': positioned }"
      :style="previewStyle"
      aria-hidden="true"
    >
      {{ previewSummary }}
    </div>
  </Teleport>
</template>

<style scoped>
/* 正文块的样式全部要走 :deep()：这些节点由 VueMarkdown 创建，拿不到本组件的作用域属性。
   选择器都收在 .markdown-answer 下，不会漏到别处。 */

.markdown-answer {
  color: var(--text-primary);
  font-size: var(--fs-base);
  line-height: var(--lh-reading);
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
  font-weight: var(--fw-semibold);
  line-height: var(--lh-heading);
}

/* 答案正文里的标题不该比页面 h1 还大：模型很爱用 `#`，照浏览器默认渲染会盖过页面层级。
   四级压到一个窄区间，靠字重和间距区分，不靠字号。 */
.markdown-answer :deep(h1) {
  font-size: var(--fs-xl);
}

.markdown-answer :deep(h2) {
  font-size: var(--fs-lg);
}

.markdown-answer :deep(h3),
.markdown-answer :deep(h4) {
  font-size: var(--fs-base);
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
  font-weight: var(--fw-bold);
}

.markdown-answer :deep(a) {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-answer :deep(a:hover) {
  color: var(--accent-hover);
}

/* 引用上标小丸（2026-09 重设计 P3）：11px 只放「[1]」这种数字，不违反中文 12px
   下限；上标 + accent-soft 底让它像正文里的一枚注脚，不打断阅读流。 */
.markdown-answer :deep(.verified-citation) {
  position: relative;
  margin: 0 1px;
  padding: 1px 5px;
  border-radius: var(--radius-pill);
  color: var(--accent);
  background: var(--accent-soft);
  font-family: var(--mono-font);
  font-size: 11px;
  font-weight: var(--fw-semibold);
  line-height: 1.4;
  text-decoration: none;
  vertical-align: super;
  white-space: nowrap;
}

/* 悬停预览卡的皮肤在共享层 popover.css（.citation-preview，与下拉面板同一族浮层表面）：
   它由 Teleport 挂到 body，不在这棵组件树里。 */

/* 行内码与代码块共用等宽字体，但底色不同：行内的要在正文流里可辨认又不打断阅读，
   块级的要像一个独立区域。 */
.markdown-answer :deep(code) {
  padding: 0.12em 0.34em;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-sunken);
  font-family: var(--mono-font);
  font-size: 0.86em;
}

.markdown-answer :deep(pre) {
  max-height: 420px;
  overflow: auto;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-base);
}

/* pre 里的 code 要退掉行内码那套底色与内边距，否则代码块里会套一层灰底。 */
.markdown-answer :deep(pre code) {
  padding: 0;
  border-radius: 0;
  background: none;
  font-size: var(--fs-xs);
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
  font-size: var(--fs-sm);
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
  font-weight: var(--fw-semibold);
  white-space: nowrap;
}

/* 斑马纹：隔行垫半透凹面，长表扫行不串行（2026-09 重设计 §三.6）。 */
.markdown-answer :deep(tbody tr:nth-child(even)) {
  background: color-mix(in srgb, var(--surface-sunken) 50%, transparent);
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
