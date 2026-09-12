<script setup lang="ts">
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
 * 2026-09 重设计：整行箭头列表改为安静的 2×2 小卡。建议只是「顺手一点」的入口，
 * 不该抢输入坞的主角地位；窄容器自然回退单列。
 */
</script>

<template>
  <ul class="suggestion-list" :aria-label="ariaLabel">
    <li v-for="example in examples" :key="example">
      <button type="button" class="suggestion-button" @click="emit('select', example)">
        <span>{{ example }}</span>
      </button>
    </li>
  </ul>
</template>

<style scoped>
.suggestion-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.suggestion-button {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 48px;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: var(--fs-sm);
  line-height: var(--lh-ui);
  text-align: left;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

.suggestion-button:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
  background: var(--surface-hover);
  transform: translateY(-1px);
}

.suggestion-button:active {
  transform: translateY(0);
  transition-duration: calc(var(--duration-fast) / 2);
}

@container (max-width: 560px) {
  .suggestion-list {
    grid-template-columns: 1fr;
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
