<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Check, CircleAlert, Copy, RotateCcw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import type { AgentTurn } from '../model/conversation'
import type { DocumentEvidence } from '@/api/agent-evidence'
import { scopeLabel } from '@/api/knowledge-scope'
import AgentToolTraceList from './AgentToolTraceList.vue'
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

function openCitation(id: string, trigger: HTMLElement): void {
  const evidence = props.turn.citations?.find((item) => item.citation_id === id)
  if (evidence) emit('open-evidence', evidence, trigger)
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

onBeforeUnmount(() => clearTimeout(copiedTimer))
</script>

<template>
  <!-- 去气泡（2026-09 重设计 P3）：提问是通栏标题块，回答跟在 32px 之下，
       角色身份靠排版顺序本身表达，不再画头像与气泡框——我们是文档问答，
       通栏比气泡适合阅读。 -->
  <article class="turn" :class="`is-${turn.status}`">
    <!-- 用文本插值渲染，不用 v-html：这段是用户原文，注入 HTML 会直接变成 XSS。 -->
    <h3 class="question-text">{{ turn.question }}</h3>

    <div class="answer-region">
      <p v-if="turn.status === 'cancelled' || turn.status === 'incomplete'" class="turn-state-line">
        <span class="turn-state">{{ turn.status === 'cancelled' ? '已停止' : '回答未完成' }}</span>
      </p>

      <p v-if="turn.scope" class="run-scope">本次范围：{{ scopeLabel(turn.scope) }}</p>
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
        @citation="openCitation"
      />
      <p v-else-if="isUnanswered" class="unanswered">
        {{ hasTracesOnly ? '模型未给出文字回答。' : '这一轮没有留下回答。' }}
        <span v-if="hasTracesOnly" class="trace-hint">已执行的检索结果见上方。</span>
      </p>

      <ol v-if="turn.citations?.length" class="citation-list" aria-label="回答引用">
        <li v-for="(citation, index) in turn.citations" :key="citation.citation_id">
          <button
            type="button"
            @click="openCitation(citation.citation_id, $event.currentTarget as HTMLElement)"
          >
            <span class="citation-number">[{{ index + 1 }}]</span>
            <span>{{ citation.knowledge_base_name }} · {{ citation.title }}</span>
          </button>
        </li>
      </ol>
      <p v-if="turn.invalidCitations?.length" class="citation-warning" role="status">
        部分引用未能对应本次取得的资料，无法核验，请勿据此确认结论。
      </p>
      <p v-if="turn.status === 'incomplete' && turn.answer" class="citation-warning" role="status">
        本次回答中断或达到处理上限，以上内容不完整。
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
  /* 提问与回答之间的 32px 分层是唯一的空间语言：层级靠留白，不靠卡片框。 */
  gap: 32px;
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

.turn-state-line {
  margin-bottom: 6px;
}

.turn-state {
  color: var(--warning);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
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

.citation-list button {
  display: flex;
  gap: 8px;
  width: 100%;
  padding: 6px 0;
  border: 0;
  color: var(--accent);
  background: transparent;
  text-align: left;
  font-size: var(--fs-xs);
  line-height: 1.55;
  overflow-wrap: anywhere;
  cursor: pointer;
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
  transition: opacity var(--duration-fast) var(--ease-out-smooth);
}

.answer-region:hover .answer-actions,
.answer-region:focus-within .answer-actions {
  opacity: 1;
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

@media (pointer: coarse) {
  .answer-actions {
    opacity: 1;
  }
}
</style>
