<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import BaseSpinner from './BaseSpinner.vue'

/* 全站按钮的唯一实现。
 *
 * 收编前有 17 个按钮类名散在 8 个文件里，同一个「主操作按钮」被写了四遍，
 * 高度 44px 与 gap 8px 靠人肉对齐。变体不靠新类名，靠 props。
 *
 * 五个变体来自实测归并，不是凭空设计：
 *   primary   实心强调底（原 .send-button / .search-button / .submit-command）
 *   secondary 下沉底 + 中性字（原 .secondary-button / .clear-button）
 *   danger    浅红底 + 红字（原 .stop-button / .cancel-command）
 *   outline   描边 + 强调字，悬停填实（原 .retry-button / .reader-retry）
 *   ghost     纯文字（原 .text-button / .expand-button / .admin-link）
 */

type Variant = 'primary' | 'secondary' | 'danger' | 'outline' | 'ghost'
type Size = 'md' | 'sm' | 'xs'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    /** 转圈并禁用。文案保留，避免按钮宽度在加载时跳动。 */
    loading?: boolean
    disabled?: boolean
    /** 只有图标时置 true：改为正方形，并要求 aria-label。 */
    iconOnly?: boolean
    /** 撑满父容器的主轴。原来靠各处写 `flex: 1`。 */
    block?: boolean
    type?: 'button' | 'submit' | 'reset'
    /** 给出 to 就渲染成 RouterLink；此时 type/disabled/loading 不适用。 */
    to?: string
  }>(),
  {
    variant: 'secondary',
    size: 'md',
    loading: false,
    disabled: false,
    iconOnly: false,
    block: false,
    type: 'button',
    to: undefined,
  },
)

const isLink = computed(() => props.to !== undefined)
// loading 期间也要挡住点击，否则双击会发两次请求。
const isBlocked = computed(() => props.disabled || props.loading)

const classes = computed(() => [
  'base-button',
  `is-${props.variant}`,
  `is-${props.size}`,
  { 'is-icon-only': props.iconOnly, 'is-block': props.block, 'is-loading': props.loading },
])

const spinnerSize = computed(() => (props.size === 'md' ? 18 : 15))
</script>

<template>
  <RouterLink v-if="isLink" :to="to!" :class="classes">
    <slot name="icon" />
    <slot />
  </RouterLink>
  <button
    v-else
    :class="classes"
    :type="type"
    :disabled="isBlocked"
    :aria-busy="loading || undefined"
  >
    <BaseSpinner v-if="loading" :size="spinnerSize" />
    <slot v-else name="icon" />
    <slot />
  </button>
</template>

<style scoped>
/* scoped 块不进 @layer，级联上总是压过 styles/components/*.css，
   所以基础组件不必担心被共享层覆盖。 */
.base-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-family: inherit;
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    color 150ms ease,
    transform 150ms ease;
}

.base-button:disabled {
  cursor: not-allowed;
}

/* 加载中是「等一下」，不是「不可用」，所以指针不用 not-allowed。
   这条要排在上面那条之后才能压过它。 */
.base-button.is-loading {
  cursor: wait;
}

/* 尺寸。高度与 gap 原来散落在各文件里，靠人肉对齐。 */
.is-md {
  gap: 8px;
  height: 44px;
  padding: 0 16px;
  font-size: 0.85rem;
  font-weight: 760;
}

.is-sm {
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  font-size: 0.78rem;
  font-weight: 720;
}

.is-xs {
  gap: 6px;
  min-height: 28px;
  padding: 0;
  font-size: 0.74rem;
  font-weight: 700;
}

.is-icon-only.is-md {
  width: 44px;
  padding: 0;
}

.is-icon-only.is-sm {
  width: 38px;
  padding: 0;
}

.is-block {
  flex: 1;
  width: 100%;
}

/* 变体。 */
.is-primary {
  border-color: var(--accent);
  color: var(--text-on-accent);
  background: var(--accent);
}

.is-primary:hover:not(:disabled) {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
  transform: translateY(-1px);
}

.is-secondary {
  color: var(--text-secondary);
  background: var(--surface-sunken);
}

.is-secondary:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--surface-sunken-hover);
}

.is-danger {
  color: var(--danger);
  background: var(--danger-soft);
}

.is-danger:hover:not(:disabled) {
  border-color: var(--danger);
}

.is-outline {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--surface-raised);
}

.is-outline:hover:not(:disabled) {
  border-color: var(--accent-hover);
  color: var(--text-on-accent);
  background: var(--accent-hover);
}

.is-ghost {
  color: var(--accent);
  background: transparent;
}

.is-ghost:hover:not(:disabled) {
  color: var(--accent-hover);
}

/* 禁用与加载。加载中不压暗到禁用那么狠：内容仍要可读。 */
.base-button:disabled:not(.is-loading) {
  opacity: 0.55;
}

.is-loading {
  opacity: 0.82;
}

.base-button:active:not(:disabled) {
  transform: translateY(1px);
}

/* 悬停位移与按下位移在 reduce 下都撤掉，只留颜色变化。 */
@media (prefers-reduced-motion: reduce) {
  .base-button {
    transition-property: background-color, border-color, color;
  }

  .base-button:hover:not(:disabled),
  .base-button:active:not(:disabled) {
    transform: none;
  }
}
</style>
