<script setup lang="ts">
import { MessagesSquare, ShieldCheck } from '@lucide/vue'
import type { AgentTurn } from '../model/conversation'
import AgentTurnCard from './AgentTurnCard.vue'

const props = defineProps<{
  turns: AgentTurn[]
  streaming: boolean
  examples: readonly string[]
}>()

const emit = defineEmits<{
  retry: []
  'choose-example': [value: string]
}>()

/** 只有最后一轮能重发：重发的是「接着当前历史再问一次」，中间轮次没有这个语义。 */
function isLast(index: number): boolean {
  return index === props.turns.length - 1
}
</script>

<template>
  <section class="transcript" aria-labelledby="transcript-title" :aria-busy="streaming">
    <h2 id="transcript-title" class="sr-only">对话记录</h2>

    <div v-if="turns.length === 0" class="empty-state">
      <span class="empty-icon" aria-hidden="true"><MessagesSquare :size="22" /></span>
      <h3>让 Agent 去查，再让它作答</h3>
      <p>
        它会自己决定要不要检索新闻库、要不要读全文，过程中的每次工具调用都会显示在回答上方。
        它只读数据，不会修改任何新闻或索引。
      </p>

      <div class="example-list">
        <p class="example-label">可以先试这些：</p>
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          class="example-button"
          @click="emit('choose-example', example)"
        >
          {{ example }}
        </button>
      </div>

      <p class="empty-note">
        <ShieldCheck :size="15" aria-hidden="true" />
        回答由模型生成，可能有误；请按它给出的来源核对原文。
      </p>
    </div>

    <div v-else class="turn-list">
      <AgentTurnCard
        v-for="(turn, index) in turns"
        :key="turn.id"
        :turn="turn"
        :can-retry="isLast(index) && !streaming"
        @retry="emit('retry')"
      />
    </div>
  </section>
</template>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.turn-list {
  display: grid;
  gap: 16px;
}

.empty-state {
  padding: 40px 30px 34px;
  border: 1px dashed var(--paper-300);
  border-radius: var(--radius-md);
  background: var(--paper-50);
  text-align: center;
}

.empty-icon {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  margin: 0 auto 16px;
  border-radius: 50%;
  color: var(--signal-600);
  background: var(--source-100);
}

.empty-state h3 {
  color: var(--ink-950);
  font-family: var(--display-font);
  font-size: 1.2rem;
  font-weight: 760;
}

.empty-state > p {
  max-width: 46ch;
  margin: 11px auto 0;
  color: var(--ink-700);
  font-size: 0.86rem;
  line-height: 1.72;
}

.example-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 9px;
  margin-top: 22px;
}

.example-label {
  width: 100%;
  color: var(--ink-500);
  font-size: 0.72rem;
  font-weight: 700;
}

.example-button {
  padding: 9px 14px;
  border: 1px solid var(--paper-300);
  border-radius: 999px;
  color: var(--ink-800);
  background: var(--paper-50);
  font-size: 0.78rem;
  transition:
    border-color 150ms ease,
    color 150ms ease,
    background-color 150ms ease;
}

.example-button:hover {
  border-color: var(--signal-600);
  color: var(--signal-600);
  background: var(--paper-100);
}

.empty-note {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 26px;
  padding-top: 17px;
  border-top: 1px solid var(--paper-200);
  color: var(--ink-500);
  font-size: 0.72rem;
}

.empty-note svg {
  flex: 0 0 auto;
  color: var(--source-600);
}

@media (max-width: 560px) {
  .empty-state {
    padding: 30px 18px 26px;
  }
}
</style>
