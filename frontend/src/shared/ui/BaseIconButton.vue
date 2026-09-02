<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseSpinner from './BaseSpinner.vue'

/* 纯图标按钮。
 *
 * 与 BaseButton 的 ghost + iconOnly 不同：这个默认透明无边，悬停才浮出描边与底色，
 * 用在密集的表格行与顶栏里。合进 BaseButton 会让那边的变体矩阵多出一维，
 * 而两者的取舍方向本来就相反——那个要显眼，这个要退让。
 *
 * label 是必填而非可选：没有可见文案的按钮，读屏用户只会听到「按钮」。
 * 类型上强制，比写在注释里靠人记得住。
 */

const props = withDefaults(
  defineProps<{
    label: string
    /* lg 给浮层/抽屉的头部收纳键：那种位置只有一个按钮，点击目标该比表格行里的大。
       md 是密集列表里的默认，sm 用在行内。 */
    size?: 'lg' | 'md' | 'sm'
    loading?: boolean
    disabled?: boolean
    /** 忙时鼠标指针用 wait 而不是 not-allowed：表达「等一下」而非「不可用」。 */
    busyCursor?: boolean
    type?: 'button' | 'submit' | 'reset'
  }>(),
  {
    size: 'md',
    loading: false,
    disabled: false,
    busyCursor: false,
    type: 'button',
  },
)

const isBlocked = computed(() => props.disabled || props.loading)
const spinnerSize = computed(() => (props.size === 'sm' ? 14 : 16))

const el = ref<HTMLButtonElement | null>(null)

/* 浮层打开时要把焦点送到收纳键上，所以对外暴露 focus。
   让调用方去摸 $el 也能做到，但那要求调用方知道根节点正好是 button。 */
function focus(): void {
  el.value?.focus()
}

defineExpose({ focus })
</script>

<template>
  <button
    ref="el"
    class="base-icon-button"
    :class="[`is-${size}`, { 'is-busy-cursor': busyCursor }]"
    :type="type"
    :disabled="isBlocked"
    :aria-label="label"
    :title="label"
    :aria-busy="loading || undefined"
  >
    <BaseSpinner v-if="loading" :size="spinnerSize" />
    <slot v-else />
  </button>
</template>

<style scoped>
.base-icon-button {
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
  transition:
    border-color 150ms ease,
    color 150ms ease,
    background-color 150ms ease;
}

.is-lg {
  width: 44px;
  height: 44px;
}

.is-md {
  width: 40px;
  height: 40px;
}

.is-sm {
  width: 34px;
  height: 34px;
}

.base-icon-button:hover:not(:disabled) {
  border-color: var(--border-subtle);
  color: var(--accent);
  background: var(--surface-base);
}

.base-icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.is-busy-cursor:disabled {
  cursor: wait;
}
</style>
