<script setup lang="ts">
withDefaults(
  defineProps<{
    modelValue: string
    rows?: number
    /** 等宽字体档：给提示词、cron、JSON 参数这类「结构化文本」用。 */
    mono?: boolean
    disabled?: boolean
    placeholder?: string
  }>(),
  { rows: 4, mono: false, disabled: false, placeholder: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

/**
 * 全站多行文本输入的唯一实现，皮肤与 BaseInput 同一档。多行高度、resize
 * 方向是本组件的职责，调用方不再各自手写。
 */
function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}
</script>

<template>
  <textarea
    class="base-textarea"
    :class="{ 'is-mono': mono }"
    :rows="rows"
    :value="modelValue"
    :disabled="disabled"
    :placeholder="placeholder"
    v-bind="$attrs"
    @input="onInput"
  ></textarea>
</template>

<style scoped>
.base-textarea {
  display: block;
  width: 100%;
  min-height: 96px;
  padding: 10px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  line-height: 1.65;
  resize: vertical;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth);
}

.base-textarea.is-mono {
  font-family: var(--mono-font);
  font-size: 0.78rem;
  line-height: 1.6;
}

.base-textarea::placeholder {
  color: var(--text-tertiary);
}

.base-textarea:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.base-textarea[aria-invalid='true'] {
  border-color: var(--danger);
}

.base-textarea:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>
