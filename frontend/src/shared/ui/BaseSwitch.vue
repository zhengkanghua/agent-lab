<script setup lang="ts">
/* 开关控件（账号目录与任务目录共用）。
 *
 * 收编前是两个类名不同、实现相同的版本：`.switch-control`（user-admin）与
 * `.switch-track`（scheduled-jobs），尺寸 32×18、旋钮 12px、位移 14px、过渡、
 * 聚焦环、禁用态逐项一致，改一处必须记得改另一处。变体不靠类名，靠 props。
 *
 * 值不由控件自己记：点击先改了原生 checkbox，这里立刻把它拨回父级给的 checked，
 * 再把请求值抛出去。父级拿到接口确认的新值后重新流回来——于是「界面显示成功、
 * 实际没生效」不会发生（两处调用方原本各自手写这段，逻辑相同）。
 *
 * checkbox 用 1px 压住而不是 display:none：后者会把它从可达性树里摘掉，
 * 读屏与键盘就再也操作不到它。track 只是它的外观。
 */

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    checked: boolean
    /** 读屏播报的控件名，如「xxx@example.com 使用状态」。 */
    label: string
    disabled?: boolean
    /** 悬停提示。落在 label 上：input 只有 1px，它自己带的 title 悬停不到。 */
    title?: string
  }>(),
  { disabled: false, title: undefined },
)

const emit = defineEmits<{ change: [requested: boolean] }>()

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const requested = input.checked
  input.checked = props.checked
  emit('change', requested)
}
</script>

<template>
  <label class="base-switch" :class="{ 'is-disabled': disabled }" :title="title">
    <!-- $attrs 挂到 input 而不是根节点：调用方给的 data-testid 要能取到真正的控件，
         取到 label 就没法在测试里拨动它。 -->
    <input
      v-bind="$attrs"
      type="checkbox"
      :checked="checked"
      :disabled="disabled"
      :aria-label="label"
      @change="onChange"
    />
    <span class="base-switch-track" aria-hidden="true"></span>
  </label>
</template>

<style scoped>
.base-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

.base-switch.is-disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.base-switch input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.base-switch-track {
  position: relative;
  display: block;
  width: 32px;
  height: 18px;
  flex: 0 0 auto;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  background: var(--surface-sunken);
  transition: background var(--duration-fast) var(--ease-out-smooth);
}

.base-switch-track::after {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--surface-raised);
  box-shadow: var(--shadow-inset-chip);
  content: '';
  transition: transform var(--duration-fast) var(--ease-out-smooth);
}

.base-switch input:checked + .base-switch-track {
  border-color: var(--accent);
  background: var(--accent);
}

.base-switch input:checked + .base-switch-track::after {
  transform: translateX(14px);
}

.base-switch input:focus-visible + .base-switch-track {
  outline: 3px solid var(--accent-ring);
  outline-offset: 2px;
}

/* 触屏：32×18 的轨道是给鼠标精度的尺寸，手指点不中。轨道等比放大到可点高度那一档，
   滑块尺寸与位移跟着改，否则会从轨道里滑出去。 */
@media (pointer: coarse) {
  .base-switch-track {
    width: 44px;
    height: 26px;
  }

  .base-switch-track::after {
    top: 3px;
    left: 3px;
    width: 18px;
    height: 18px;
  }

  .base-switch input:checked + .base-switch-track::after {
    transform: translateX(18px);
  }
}
</style>
