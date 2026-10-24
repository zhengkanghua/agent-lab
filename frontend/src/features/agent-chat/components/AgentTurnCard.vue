<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Check, CircleAlert, Copy, RotateCcw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import type { AgentTurn } from '../model/conversation'
import type { DocumentEvidence } from '@/api/agent-evidence'
import { excerptFor, type CitationExcerpt } from '../model/citation-excerpt'
import { scopeLabel } from '@/api/knowledge-scope'
import AgentToolTraceList from './AgentToolTraceList.vue'
import AgentEvidencePanel from './AgentEvidencePanel.vue'
import SafeMarkdown from '@/shared/ui/SafeMarkdown.vue'

const props = defineProps<{ turn: AgentTurn; canRetry: boolean }>()

const emit = defineEmits<{
  retry: []
  'open-evidence': [evidence: DocumentEvidence, trigger: HTMLElement]
}>()

const citationIds = computed(() => (props.turn.citations ?? []).map((item) => item.citation_id))

/* 引用小丸 hover 时展示的摘要（来源 · 标题），由 SafeMarkdown 落到 data-summary。 */
const citationSummaries = computed(() => {
  const map: Record<string, string> = {}
  for (const citation of props.turn.citations ?? []) {
    map[citation.citation_id] = `${citation.knowledge_base_name} · ${citation.title}`
  }
  return map
})

/* 一度展开的面板。面板状态放在这一轮组件里而不是往上提：一轮之内只有一个，两轮之间互不
   干扰（上一轮的引用在上一轮展开，不会挤进这一轮的列表）。 */
interface ActivePanel {
  citationId: string
  evidence: DocumentEvidence
  excerpt: CitationExcerpt
}

const activePanel = ref<ActivePanel | null>(null)
/* 由正文内联小丸打开的那一条：只有它需要被滚进视口——列表里点的那条本来就在眼前。 */
const scrollTargetId = ref<string | null>(null)
/* 本轮卡片的根节点：用来判断一次点击是不是落在「这一轮里」。 */
const rootEl = ref<HTMLElement | null>(null)
/* Esc 收起后焦点还给触发元素；点面板外收起则不还，焦点跟着用户新的落点走。 */
let lastTrigger: HTMLElement | null = null
let listening = false

/** 面板的 DOM id，同时写进触发按钮的 aria-controls。 */
function panelId(citationId: string): string {
  return `evidence-panel-${citationId}`
}

/* 引用列表条目与正文内联小丸走同一个入口。
 *
 * 切不出当初那段时**直接打开当前全文**，与面板落地前的行为一致：不展开一个空面板，
 * 也不加任何解释文案——那种文案会把「这一轮没留下原文」说成界面故障。 */
function openCitation(citationId: string, trigger: HTMLElement, fromAnswer = false): void {
  const evidence = props.turn.citations?.find((item) => item.citation_id === citationId)
  if (!evidence) return
  if (activePanel.value?.citationId === citationId) {
    closePanel()
    return
  }
  const excerpt = excerptFor(citationId, props.turn.traces)
  if (!excerpt) {
    emit('open-evidence', evidence, trigger)
    return
  }
  activePanel.value = { citationId, evidence, excerpt }
  scrollTargetId.value = fromAnswer ? citationId : null
  lastTrigger = trigger
  attachDismissListeners()
}

/** 面板底部「查看当前全文」：沿用既有阅读链路，`DocumentReader` 那边一行不动。 */
function openFulltext(trigger: HTMLElement): void {
  if (activePanel.value) emit('open-evidence', activePanel.value.evidence, trigger)
}

function closePanel(restoreFocus = false): void {
  if (!activePanel.value) return
  activePanel.value = null
  scrollTargetId.value = null
  detachDismissListeners()
  if (restoreFocus) lastTrigger?.focus()
  lastTrigger = null
}

/* 收起的两条额外路径：按 Esc、点面板外。监听挂在 document 上而不是面板上——焦点与指针都
   可能落在面板之外的任何地方；只在展开期间挂，收起就摘。 */
function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  closePanel(true)
}

function onDocumentClick(event: Event): void {
  const target = event.target
  if (!(target instanceof Element)) return
  /* 本轮的引用列表（面板本身在列表条目里）与正文小丸，点击交给它们自己的处理：在这里先收起
     再让点击重开，会让「再点同一条即收起」变成关不掉。
     判断「本轮」必须是卡片根节点里的那一份：别的轮次的引用按钮与内联小丸确实在这个面板
     之外，点它们就该收起——否则两轮各开一个面板，同时摆在对话流里。 */
  if (rootEl.value?.contains(target) && target.closest('.citation-list, .verified-citation')) return
  closePanel()
}

function attachDismissListeners(): void {
  if (listening) return
  listening = true
  document.addEventListener('keydown', onDocumentKeydown)
  document.addEventListener('click', onDocumentClick)
}

function detachDismissListeners(): void {
  if (!listening) return
  listening = false
  document.removeEventListener('keydown', onDocumentKeydown)
  document.removeEventListener('click', onDocumentClick)
}

const isStreaming = computed(() => props.turn.status === 'streaming')

/** 还没有任何 token、也还没报错时显示占位提示，避免出现一片空白答案区。 */
const isThinking = computed(() => isStreaming.value && props.turn.answer.length === 0)

/**
 * 这一轮结束了，但既没有回答也没有错误可显示。
 *
 * 两个来源：回放历史里首轮就失败的会话（checkpointer 只存下了提问），以及流刚开始就被停掉的
 * 那一轮。少了这句说明，回答区是一片空白，看起来像界面坏了。不说成「出错了」
 * ——历史里没有存下当时的原因，说具体错误会误导排查。
 */
const isUnanswered = computed(
  () => !isStreaming.value && props.turn.answer.length === 0 && props.turn.error === null,
)

/** 有工具轨迹但没有文字回答——通常是模型故障或上游返回空内容。 */
const hasTracesOnly = computed(() => isUnanswered.value && props.turn.traces.length > 0)

/* 复制成功的反馈就地落在按钮文字上，两秒后还原；期间再点无害（内容相同）。 */
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined

async function copyAnswer(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.turn.answer)
  } catch {
    return
  }
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), 2000)
}

onBeforeUnmount(() => {
  clearTimeout(copiedTimer)
  detachDismissListeners()
})
</script>

<template>
  <!-- 去气泡（2026-09 重设计 P3）：提问是通栏标题块，回答跟在其下，角色身份靠排版顺序
       本身表达，不再画头像与气泡框——我们是文档问答，通栏比气泡适合阅读。
       2026-10 补左侧时间线：没有气泡就没有「靠右对齐」那种一眼可辨的位置线索，
       一轮轮往上翻时找不到自己的提问。圆点 + 竖线把这条线索还回来（与检索流同一套，
       见 styles/components/stream.css），代价是下面那条间距规则要跟着改。 -->
  <article ref="rootEl" class="turn stream-item" :class="`is-${turn.status}`">
    <span class="stream-mark" aria-hidden="true"></span>
    <!-- 用文本插值渲染，不用 v-html：这段是用户原文，注入 HTML 会直接变成 XSS。 -->
    <h3 class="question-text">{{ turn.question }}</h3>

    <div class="answer-region">
      <p v-if="turn.scope" class="run-scope">本次范围：{{ scopeLabel(turn.scope) }}</p>
      <!-- 这一轮实际用的模型。名字取当轮冻结的那份快照，所以条目后来改名或停用都不会改写
           已经发生过的这几轮；流式中还没回放，此时不显示，由选择器那边说当前用的是哪个。 -->
      <p v-if="turn.llmModel" class="run-scope">本次模型：{{ turn.llmModel.display_name }}</p>
      <AgentToolTraceList :traces="turn.traces" :streaming="isStreaming" />

      <p v-if="isThinking" class="thinking" aria-live="polite">正在思考…</p>
      <!-- 答案与文件阅读器共用 SafeMarkdown；提问和工具轨迹保持原文展示。 -->
      <SafeMarkdown
        v-else-if="turn.answer"
        class="answer-body"
        :markdown="turn.answer"
        :streaming="isStreaming"
        :citation-ids="citationIds"
        :citation-summaries="citationSummaries"
        @citation="(id, trigger) => openCitation(id, trigger, true)"
      />
      <p v-else-if="isUnanswered" class="unanswered">
        {{ hasTracesOnly ? '模型未给出文字回答。' : '这一轮没有留下回答。' }}
        <span v-if="hasTracesOnly" class="trace-hint">已执行的检索结果见上方。</span>
      </p>

      <!-- 面板插在被点中的那一条**下方**（li 之内、button 之后），不是整份列表之后，也不是
           答案与列表之间：点哪条就在哪条下面展开，视线不跳，被点的元素自身也不移位。 -->
      <ol v-if="turn.citations?.length" class="citation-list" aria-label="回答引用">
        <li v-for="(citation, index) in turn.citations" :key="citation.citation_id">
          <!-- aria-controls 只在展开时挂：面板用 v-if 控制存在，收起后那个 id 在页面上
               并不存在，指着一个不存在的元素对读屏是空承诺。 -->
          <button
            type="button"
            :aria-expanded="activePanel?.citationId === citation.citation_id"
            :aria-controls="
              activePanel?.citationId === citation.citation_id
                ? panelId(citation.citation_id)
                : undefined
            "
            @click="openCitation(citation.citation_id, $event.currentTarget as HTMLElement)"
          >
            <span class="citation-number">[{{ index + 1 }}]</span>
            <span>{{ citation.knowledge_base_name }} · {{ citation.title }}</span>
          </button>
          <AgentEvidencePanel
            v-if="activePanel && activePanel.citationId === citation.citation_id"
            :panel-id="panelId(citation.citation_id)"
            :evidence="activePanel.evidence"
            :body="activePanel.excerpt.body"
            :scroll-into-view="scrollTargetId === citation.citation_id"
            @open-fulltext="openFulltext"
          />
        </li>
      </ol>
      <p v-if="turn.invalidCitations?.length" class="citation-warning" role="status">
        部分引用未能对应本次取得的资料，无法核验，请勿据此确认结论。
      </p>

      <BaseCallout
        v-if="turn.error"
        class="turn-error"
        tone="danger"
        :title="turn.error.title"
        :description="turn.error.description"
      >
        <template #icon><CircleAlert :size="15" aria-hidden="true" /></template>
        <template #actions>
          <BaseButton
            v-if="turn.error.retryable && canRetry"
            class="retry-button"
            variant="outline"
            size="sm"
            @click="emit('retry')"
          >
            <template #icon><RotateCcw :size="15" aria-hidden="true" /></template>
            重发这一轮
          </BaseButton>
        </template>
      </BaseCallout>

      <!-- 每轮回答的操作键：悬停或键盘聚焦才浮现，触屏常驻。
           流式中不给——没写完的答案不值得复制，重试由停止键承接。 -->
      <div v-if="!isStreaming && (turn.answer || canRetry)" class="answer-actions">
        <button v-if="turn.answer" type="button" class="ghost-action" @click="copyAnswer">
          <Check v-if="copied" :size="14" aria-hidden="true" />
          <Copy v-else :size="14" aria-hidden="true" />
          {{ copied ? '已复制' : '复制' }}
        </button>
        <button
          v-if="canRetry && (!turn.error || turn.error.retryable)"
          type="button"
          class="ghost-action"
          @click="emit('retry')"
        >
          <RotateCcw :size="14" aria-hidden="true" />
          重试
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.turn {
  display: grid;
  /* 轮内 16px、轮间 32px（轮间那条在 AgentTranscript 的 .turn-list 上）：
     挨得近的才成一组。原来是反的——轮内 32px 比轮间 16px 还大，
     于是「上一轮的回答 + 这一轮的提问」看起来才是一对，正好读反。 */
  gap: var(--space-4);
}

/* 圆点落在 18px 提问标题的首行中心（共享层默认的 top:19px 是给检索页那个 44px 标题行
   算的）；竖线相应地从圆点下沿起画。scoped 不分层，压得过 @layer components。 */
.turn .stream-mark {
  top: 9px;
}

.turn::before {
  top: 19px;
}

/* 提问按用户原文保留换行；18/600 让它在阅读流里像一节小标题。 */
.question-text {
  color: var(--text-primary);
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-heading);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.answer-region {
  min-width: 0;
}

.run-scope {
  margin-bottom: 10px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}

.citation-list {
  display: grid;
  gap: 5px;
  margin: 16px 0 0;
  padding: 12px 0 0;
  border-top: 1px solid var(--border-subtle);
  list-style: none;
}

/* 引用行是「点开全文对照」的入口，数量多、密度高，靠颜色本身不足以说明它可点。
   悬停时补一层底，并让底比文字各宽出 8px——pill 有呼吸空间，[1] 又仍与正文左对齐。 */
.citation-list button {
  display: flex;
  gap: 8px;
  width: calc(100% + 16px);
  margin: 0 -8px;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: transparent;
  text-align: left;
  font-size: var(--fs-xs);
  line-height: 1.55;
  overflow-wrap: anywhere;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.citation-list button:hover {
  color: var(--accent-hover);
  background: var(--surface-hover);
}

/* 按下时沉回去：已经按下去了，不该还浮着。 */
.citation-list button:active {
  background: var(--surface-sunken);
}

.citation-number {
  flex-shrink: 0;
  font-family: var(--mono-font);
}
.citation-warning {
  margin-top: 12px;
  color: var(--warning);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.thinking {
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
}

/* 与 .thinking 同一档视觉重量：两者都是「这里本该有内容」的中性说明，不该比真实回答更显眼。 */
.unanswered {
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
  font-style: italic;
}

.trace-hint {
  display: block;
  margin-top: 4px;
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
}

/* 错误面板本体归 BaseCallout；这里只留外边距。 */
.turn-error {
  margin-top: 12px;
}

/* 描边、字号、悬停填实都归 BaseButton 的 outline 变体——第二步就是从这个类
   与 .reader-retry 归并出那个变体的，只是当时漏了这个调用方。留下的只有外边距。 */
.retry-button {
  margin-top: 11px;
}

/* 复制 / 重试是低频动作，常驻只会变成一排小图标；悬停与键盘聚焦浮现，
   触屏没有 hover，常驻（同 ThreadListItem 删除键的先例）。 */
.answer-actions {
  display: flex;
  gap: 2px;
  margin-top: 10px;
  opacity: 0;
  /* 同 ThreadListItem：透明仍吃点击，隐藏期间关掉指针命中，但不退出 Tab 序。 */
  pointer-events: none;
  transition: opacity var(--duration-fast) var(--ease-out-smooth);
}

.answer-region:hover .answer-actions,
.answer-region:focus-within .answer-actions {
  opacity: 1;
  pointer-events: auto;
}

.ghost-action {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: var(--fs-xs);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.ghost-action:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

/* 复制与重试按下时收一档底。这两个动作的回执都不显眼（复制只换一下图标、重试要等一整轮），
   按下的这一下是唯一即时的确认。 */
.ghost-action:active {
  background: var(--surface-sunken-hover);
}

/* 触屏：常驻之外还要撑到可点高度。32px 是为鼠标精度换来的密集尺寸，
   手指点不中，只有 opacity: 1 让它们可见但没有变好点。 */
@media (pointer: coarse) {
  .answer-actions {
    opacity: 1;
    pointer-events: auto;
  }

  .ghost-action {
    min-height: var(--tap-target);
    padding: 4px 12px;
  }
}

/* 窄屏收窄左侧留白，与检索流同一档——手机上没有 32px 可以白让。 */
@container (max-width: 560px) {
  .turn {
    padding-left: var(--space-5);
  }
}
</style>
