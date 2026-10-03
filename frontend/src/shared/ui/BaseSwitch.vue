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
  transition:
    background-color var(--duration-fast) var(--ease-out-smooth),
    border-color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

/* 轨道自己回应鼠标。它只有 32×18，落在密集表格里本就不显眼；有的表连行悬停都没有
   （知识库目录），开关是那一行唯一的交互点，划过去毫无反应就完全看不出能点。
   关掉的轨道靠描边加深，打开的靠强调色再深一档——两个方向都是「更明确」，不是「按下」。
   禁用态不响应：那是在说「现在改不了」，给反馈反而是误导。 */
.base-switch:not(.is-disabled):hover .base-switch-track {
  border-color: var(--border-strong);
  background: var(--surface-sunken-hover);
}

.base-switch:not(.is-disabled):hover input:checked + .base-switch-track {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
}

/* 按下的一下。悬停已经把颜色用掉了（关→描边加深、开→强调色再深一档），
   按压只能换个维度：整条轨道缩一档。开关没有位移可用，也不该在按下时改色——
   那会和「已打开」的实色状态撞车，看上去像状态提前变了。
   scale 与全站按钮的按下语言一致（BaseButton 0.98、BaseIconButton 0.95），
   这里是全站最小的一个控件，缩得比按钮狠一点才看得出来。
   写在两条 hover 之后，按下时才能压过它们。 */
.base-switch:not(.is-disabled):active .base-switch-track {
  transform: scale(0.9);
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
