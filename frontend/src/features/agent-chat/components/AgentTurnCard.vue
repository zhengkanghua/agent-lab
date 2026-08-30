<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, RotateCcw, Sparkles, UserRound } from '@lucide/vue'
import type { AgentTurn } from '../model/conversation'
import AgentToolTraceList from './AgentToolTraceList.vue'

const props = defineProps<{ turn: AgentTurn; canRetry: boolean }>()

const emit = defineEmits<{ retry: [] }>()

const isStreaming = computed(() => props.turn.status === 'streaming')

/** 还没有任何 token、也还没报错时显示占位提示，避免出现一张空白答案卡。 */
const isThinking = computed(() => isStreaming.value && props.turn.answer.length === 0)
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

        <AgentToolTraceList :traces="turn.traces" />

        <p v-if="isThinking" class="thinking" aria-live="polite">正在思考…</p>
        <!-- 同样走文本插值：模型输出里可能带 Markdown 或 HTML 片段，当纯文本渲染。 -->
        <p v-else-if="turn.answer" class="answer-text" :class="{ 'is-streaming': isStreaming }">
          {{ turn.answer }}
        </p>

        <div v-if="turn.error" class="turn-error" role="alert">
          <p class="error-title">
            <CircleAlert :size="15" aria-hidden="true" />
            {{ turn.error.title }}
          </p>
          <p class="error-description">{{ turn.error.description }}</p>
          <button
            v-if="turn.error.retryable && canRetry"
            type="button"
            class="retry-button"
            @click="emit('retry')"
          >
            <RotateCcw :size="15" aria-hidden="true" />
            重发这一轮
          </button>
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
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-md);
  background: var(--paper-50);
}

.turn.is-error {
  border-color: var(--danger-100);
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
  color: var(--paper-50);
  background: var(--source-500);
}

.answer .role-icon {
  background: var(--signal-500);
}

.bubble-body {
  min-width: 0;
}

.role-name {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--ink-500);
  font-size: 0.7rem;
  font-weight: 720;
}

.turn-state {
  color: var(--warning-600);
  font-weight: 650;
}

.question-text,
.answer-text {
  color: var(--ink-950);
  font-size: 0.92rem;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.question-text {
  color: var(--ink-800);
}

/* 流式期间在末尾加一个光标块，让「还在写」这件事不依赖别处的加载指示。 */
.answer-text.is-streaming::after {
  content: '';
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 3px;
  background: var(--signal-500);
  vertical-align: text-bottom;
  animation: caret-blink 1s step-end infinite;
}

.thinking {
  color: var(--ink-500);
  font-size: 0.85rem;
}

.turn-error {
  margin-top: 12px;
  padding: 12px 13px;
  border: 1px solid var(--danger-100);
  border-radius: var(--radius-sm);
  background: #fdf6f5;
}

.error-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--danger-600);
  font-size: 0.82rem;
  font-weight: 720;
}

.error-description {
  margin-top: 5px;
  color: var(--ink-700);
  font-size: 0.78rem;
  line-height: 1.6;
}

.retry-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 11px;
  padding: 8px 13px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  color: var(--ink-800);
  background: var(--paper-50);
  font-size: 0.76rem;
  font-weight: 700;
}

.retry-button:hover {
  border-color: var(--signal-600);
  color: var(--signal-600);
}

@keyframes caret-blink {
  50% {
    opacity: 0;
  }
}
</style>
