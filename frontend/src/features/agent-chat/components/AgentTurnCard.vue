<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, RotateCcw, Sparkles, UserRound } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import type { AgentTurn } from '../model/conversation'
import AgentToolTraceList from './AgentToolTraceList.vue'
import MarkdownAnswer from './MarkdownAnswer.vue'

const props = defineProps<{ turn: AgentTurn; canRetry: boolean }>()

const emit = defineEmits<{ retry: [] }>()

const isStreaming = computed(() => props.turn.status === 'streaming')

/** 还没有任何 token、也还没报错时显示占位提示，避免出现一张空白答案卡。 */
const isThinking = computed(() => isStreaming.value && props.turn.answer.length === 0)

/**
 * 这一轮结束了，但既没有回答也没有错误可显示。
 *
 * 两个来源：回放历史里首轮就失败的会话（checkpointer 只存下了提问），以及流刚开始就被停掉的
 * 那一轮。少了这句说明，卡片上「Agent」标题下面是一片空白，看起来像界面坏了。不说成「出错了」
 * ——历史里没有存下当时的原因，说具体错误会误导排查。
 */
const isUnanswered = computed(
  () => !isStreaming.value && props.turn.answer.length === 0 && props.turn.error === null,
)

/** 有工具轨迹但没有文字回答——通常是模型故障或上游返回空内容。 */
const hasTracesOnly = computed(() => isUnanswered.value && props.turn.traces.length > 0)
</script>

<template>
  <article class="turn" :class="`is-${turn.status}`">
    <div class="bubble question">
      <span class="role-icon" aria-hidden="true"><UserRound :size="15" /></span>
      <div class="bubble-body">
        <p class="role-name">我的提问</p>
        <!-- 用文本插值渲染，不用 v-html：这段是用户原文，注入 HTML 会直接变成 XSS。 -->
        <p class="question-text">{{ turn.question }}</p>
      </div>
    </div>

    <div class="bubble answer">
      <span class="role-icon" aria-hidden="true"><Sparkles :size="15" /></span>
      <div class="bubble-body">
        <p class="role-name">
          Agent
          <span v-if="turn.status === 'cancelled'" class="turn-state">已停止</span>
        </p>

        <AgentToolTraceList :traces="turn.traces" :streaming="isStreaming" />

        <p v-if="isThinking" class="thinking" aria-live="polite">正在思考…</p>
        <!-- 答案正文是本页唯一按 Markdown 渲染的地方。安全配置的理由写在 MarkdownAnswer 里，
             一句话是：没装 rehype-raw + 开了 sanitize，裸 HTML 与 javascript: 链接都进不来。 -->
        <MarkdownAnswer
          v-else-if="turn.answer"
          class="answer-body"
          :markdown="turn.answer"
          :streaming="isStreaming"
        />
        <p v-else-if="isUnanswered" class="unanswered">
          {{ hasTracesOnly ? '模型未给出文字回答。' : '这一轮没有留下回答。' }}
          <span v-if="hasTracesOnly" class="trace-hint">已执行的检索结果见上方。</span>
        </p>

        <div v-if="turn.error" class="turn-error" role="alert">
          <p class="error-title">
            <CircleAlert :size="15" aria-hidden="true" />
            {{ turn.error.title }}
          </p>
          <p class="error-description">{{ turn.error.description }}</p>
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
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.turn {
  display: grid;
  gap: 14px;
  padding: 18px 19px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
}

.turn.is-error {
  border-color: var(--danger-soft);
}

.bubble {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 11px;
  align-items: start;
}

.role-icon {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: var(--text-secondary);
  background: var(--surface-sunken);
}

/* 提问用中性底、回答用强调底：两个头像原来是青红对撞，红收窄到报错之后，
   靠「谁是中性、谁被强调」区分，强调色留给 Agent 这一侧。 */
.answer .role-icon {
  color: var(--text-on-accent);
  background: var(--accent);
}

.bubble-body {
  min-width: 0;
}

.role-name {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 720;
}

.turn-state {
  color: var(--warning);
  font-weight: 650;
}

/* 提问保持 pre-wrap 的纯文本：它是用户原文，换行按他敲的来。
   答案正文的排版与流式光标都归 MarkdownAnswer，这里不再有 .answer-text。 */
.question-text {
  color: var(--text-secondary);
  font-size: 0.92rem;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.thinking {
  color: var(--text-muted);
  font-size: 0.85rem;
}

/* 与 .thinking 同一档视觉重量：两者都是「这里本该有内容」的中性说明，不该比真实回答更显眼。 */
.unanswered {
  color: var(--text-muted);
  font-size: 0.85rem;
  font-style: italic;
}

.trace-hint {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.turn-error {
  margin-top: 12px;
  padding: 12px 13px;
  border: 1px solid var(--danger-soft);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
}

.error-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--danger);
  font-size: 0.82rem;
  font-weight: 720;
}

.error-description {
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: 0.78rem;
  line-height: 1.6;
}

/* 描边、字号、悬停填实都归 BaseButton 的 outline 变体——第二步就是从这个类
   与 .reader-retry 归并出那个变体的，只是当时漏了这个调用方。留下的只有外边距。 */
.retry-button {
  margin-top: 11px;
}
</style>
