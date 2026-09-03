<script setup lang="ts">
import { computed, useAttrs, useId, useSlots } from 'vue'

/* 表单字段的外壳：标签、说明、错误，以及把它们连起来的 aria 关系。
 *
 * 收编的是最容易出错的那部分——`aria-describedby` 原来在每个字段里手写，
 * 值还得随「当前有没有错误」在两个 id 之间切换。漏一处不会报错、界面也照常，
 * 只是读屏用户听不到错误原因。这里算一次，通过插槽 props 交给控件。
 *
 * 控件本身不收进来：input / textarea / select 的属性面差别太大，
 * 包一层「万能控件」只会变成一堆穿透用的 prop。这里只管外壳与接线。
 */

const props = withDefaults(
  defineProps<{
    label: string
    /** 校验失败的原因。给了它就进错误态：控件标 aria-invalid，说明文字被替换。 */
    error?: string
    /** 常态下的补充说明，比如格式要求。 */
    hint?: string
    /** 视觉上标一个「必填」，同时把 required 交给控件。 */
    required?: boolean
    /** 外部已有稳定 id 时传进来（比如要被别处的 label 引用）；否则自动生成。 */
    id?: string
  }>(),
  { error: undefined, hint: undefined, required: false, id: undefined },
)

// useId() 是 Vue 3.5 的能力，SSR 与客户端产出一致，不会出现 hydration 不匹配。
const generatedId = useId()
const controlId = computed(() => props.id ?? `field-${generatedId}`)
const errorId = computed(() => `${controlId.value}-error`)
const hintId = computed(() => `${controlId.value}-hint`)

/* 防呆（仅开发期）：控件必须经默认插槽由调用方提供。曾有调用方把 v-model/type
   直接传给本组件、插槽空着，页面上只剩标签没有输入框，且无任何报错——表单整个
   静默失效。attrs 上出现控件属性而插槽为空时立刻提醒（PasswordChangeForm 踩过）。 */
if (import.meta.env.DEV) {
  const attrs = useAttrs()
  const slots = useSlots()
  const controlAttrNames = ['modelValue', 'type', 'placeholder', 'autocomplete', 'inputmode']
  if (controlAttrNames.some((name) => name in attrs) && !slots.default) {
    console.warn(
      '[BaseField] 检测到控件属性（modelValue/type/...）但没有默认插槽内容：' +
        'BaseField 只渲染标签/错误/说明外壳，控件要经 #default="{ control }" ' +
        '以 <input v-bind="control"> 渲染，传到本组件上的控件属性会被丢弃。',
    )
  }
}

const slots = defineSlots<{
  default: (props: { control: Record<string, unknown>; controlId: string }) => unknown
  /** 说明本身要带状态时用它替代 hint 字符串，两者取其一。 */
  hint?: () => unknown
}>()

const hasError = computed(() => Boolean(props.error))
// 插槽也算「有说明」，否则用插槽的字段拿不到 aria-describedby。
const hasHint = computed(() => Boolean(props.hint) || Boolean(slots.hint))

/* 错误态下只指向错误、不再指向说明：两条都念会把重点冲淡，
   而此刻用户要听的是「哪里不对」。 */
const describedBy = computed(() => {
  if (hasError.value) return errorId.value
  return hasHint.value ? hintId.value : undefined
})

const controlAttrs = computed(() => ({
  id: controlId.value,
  required: props.required || undefined,
  'aria-invalid': hasError.value || undefined,
  'aria-describedby': describedBy.value,
}))

defineExpose({ controlId })
</script>

<template>
  <div class="base-field" :class="{ 'has-error': hasError }">
    <label class="field-label" :for="controlId">
      {{ label }}
      <span v-if="required" class="required-mark" aria-hidden="true">*</span>
    </label>

    <!-- 把接好的属性交给调用方展开到真实控件上：v-bind="control" 一次到位。 -->
    <slot :control="controlAttrs" :control-id="controlId" />

    <p v-if="error" :id="errorId" class="field-error" role="alert">{{ error }}</p>
    <!-- hint 插槽给「说明本身要带状态」的场合：比如剩余字数要随接近上限变色。
         id 与 aria 接线仍归本组件，调用方只提供内容。 -->
    <p v-else-if="hasHint" :id="hintId" class="field-hint">
      <slot name="hint">{{ hint }}</slot>
    </p>
  </div>
</template>

<style scoped>
.base-field {
  display: block;
}

.field-label {
  display: block;
  margin-bottom: 7px;
  color: var(--text-secondary);
  font-size: 0.8rem;
  font-weight: 700;
}

.required-mark {
  margin-left: 3px;
  color: var(--danger);
}

.field-error {
  margin-top: 8px;
  color: var(--danger);
  font-size: 0.78rem;
  font-weight: 650;
}

.field-hint {
  margin-top: 8px;
  color: var(--text-tertiary);
  font-size: 0.72rem;
  line-height: 1.55;
}
</style>
