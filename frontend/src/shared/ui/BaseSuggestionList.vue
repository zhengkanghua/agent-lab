<script setup lang="ts">
import { ArrowUpRight } from '@lucide/vue'

withDefaults(
  defineProps<{
    examples: readonly string[]
    ariaLabel?: string
  }>(),
  { ariaLabel: '示例' },
)

const emit = defineEmits<{ select: [value: string] }>()

/**
 * 全站「示例建议卡」的唯一实现，收编检索页空态与 Agent 空态各写一份的同款列表。
 *
 * 整行式而不是胶囊排布：几条文案长度不一时，胶囊会在第二行留下孤儿；整行还给
 * 每条更大的点击区。行尾箭头提示「点了就有动作」。
 */
</script>

<template>
  <ul class="suggestion-list" :aria-label="ariaLabel">
    <li v-for="example in examples" :key="example">
      <button type="button" class="suggestion-button" @click="emit('select', example)">
        <span>{{ example }}</span>
        <ArrowUpRight :size="15" aria-hidden="true" />
      </button>
    </li>
  </ul>
</template>

<style scoped>
.suggestion-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.suggestion-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 14px 15px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.88rem;
  text-align: left;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-in-out-back);
}

.suggestion-button:hover {
  border-color: var(--accent);
  color: var(--accent);
  transform: translateY(-1px);
}

.suggestion-button:active {
  transform: translateY(0) scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.suggestion-button svg {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  transition: color var(--duration-fast) var(--ease-out-smooth);
}

.suggestion-button:hover svg {
  color: var(--accent);
}

@container (max-width: 560px) {
  .suggestion-button {
    padding: 12px 13px;
    font-size: 0.84rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .suggestion-button {
    transition-property: border-color, color, background-color;
  }

  .suggestion-button:hover,
  .suggestion-button:active {
    transform: none;
  }
}
</style>
