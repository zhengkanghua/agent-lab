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
 * 表单字段里的下拉选择：皮肤与 BaseInput 同一档（同高、同描边、同聚焦环、
 * 同悬停）。选项由调用方以原生 <option> 写在默认插槽里——选项本就是调用方的
 * 领域词汇，包一层「万能选项」只会多一套透传 prop。
 *
 * 宽度不由这里定死：根节点是 width: 100% 的块，需要别的宽度由调用方在自己的
 * 布局容器上给（限宽 200px、或放进 grid 格里）——子组件的根节点会继承调用方的
 * scoped 属性，所以 `.binding-cell select`、`.directory-filters select` 这样的
 * 后代选择器能直接命中它，不需要 :deep。
 *
 * 还有两个下拉没有走这里，是权衡后的例外，不是漏网：
 *   - 来源管理绑定格里的那个：它要在后端确认之前一直显示旧绑定（保存失败就不显示
 *     用户刚选的），靠调用方「把原生值拨回去、等缓存回流」实现——本组件没有这层
 *     受控回退（BaseSwitch 有，多行与单行输入都不需要）。
 *   - 文档审核的对照切换：它是视图切换器、不是数据字段，盒子比表单字段矮一档、
 *     圆角小一档，收进来本组件就要多出一个尺寸档，而它只有这一个使用方。
 * 两处都按本组件的取值逐项对齐过（同描边色、同聚焦描边转强调色、悬停加深一档），
 * 手感一致，差别只在盒子尺寸。
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
  border-radius: var(--radius-md);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth),
    opacity var(--duration-fast) var(--ease-out-smooth);
}

/* 悬停描边加深，与 BaseInput / BaseTextarea 同一档手感（:where() 归零特异性，
   见 BaseInput 里的说明）。原生 select 的悬停本来就只改箭头，不动描边。 */
.base-select:where(:not(:disabled):hover) {
  border-color: var(--border-strong);
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
