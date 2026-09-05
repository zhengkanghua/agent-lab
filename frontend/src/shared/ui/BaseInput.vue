<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    modelValue?: string | number
    type?: string
    disabled?: boolean
    placeholder?: string
  }>(),
  { modelValue: '', type: 'text', disabled: false, placeholder: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [value: string | number] }>()

/**
 * 全站文本输入框的唯一实现，收编此前散在各表单里的同款皮肤
 * （后台两套表单、两处密码表单、设置中心编辑器等）。
 *
 * 与 BaseField 配套使用：外壳（标签/错误/说明/aria 接线）归 BaseField，
 * 这里只负责控件本身。BaseField 经插槽把 id / aria-* 递进来，靠 attrs
 * 透传落到 input 上；v-bind 放在静态绑定之后，调用方仍可覆盖任意属性。
 *
 * type="number" 时抛出数值（空串保持空串），调用方不必再各写一层
 * v-model.number 的类型修补。
 */
function onInput(event: Event): void {
  const value = (event.target as HTMLInputElement).value
  if (props.type === 'number') {
    emit('update:modelValue', value === '' ? '' : Number(value))
    return
  }
  emit('update:modelValue', value)
}
</script>

<template>
  <input
    class="base-input"
    :type="type"
    :value="modelValue"
    :disabled="disabled"
    :placeholder="placeholder"
    v-bind="$attrs"
    @input="onInput"
  />
</template>

<style scoped>
.base-input {
  display: block;
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth);
}

.base-input::placeholder {
  color: var(--text-tertiary);
}

.base-input:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

/* 错误态由 BaseField 的 aria-invalid 带过来：描边变色就够，不再叠一层红环，
   与红字错误说明相互独立才不糊成一团。 */
.base-input[aria-invalid='true'] {
  border-color: var(--danger);
}

.base-input:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>
