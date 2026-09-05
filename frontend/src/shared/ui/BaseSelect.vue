<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 字符串或数字都收；change 时抛出的统一是字符串，调用方按需转换。 */
    modelValue: string | number
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

/**
 * 全站下拉选择的唯一实现，皮肤与 BaseInput 同一档（同高、同描边、同聚焦环）。
 * 选项由调用方以原生 <option> 写在默认插槽里——选项本就是调用方的领域词汇，
 * 包一层「万能选项」只会多一套透传 prop。
 */
function onChange(event: Event): void {
  emit('update:modelValue', (event.target as HTMLSelectElement).value)
}
</script>

<template>
  <select
    class="base-select"
    :value="modelValue"
    :disabled="disabled"
    v-bind="$attrs"
    @change="onChange"
  >
    <slot />
  </select>
</template>

<style scoped>
.base-select {
  display: block;
  width: 100%;
  height: 42px;
  padding: 0 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  font-weight: 700;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth);
}

.base-select:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.base-select:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>
