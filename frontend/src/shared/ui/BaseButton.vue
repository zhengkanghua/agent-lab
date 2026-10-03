<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'
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
 *   soft      浅强调底 + 强调字：表示「当前项」，与侧栏的当前导航态同源。
 *             视图切换、分段控件用它，不要用 secondary——下沉灰底会被读成「按下去的禁用态」，
 *             于是选中的那一项反而是整排里最不显眼的（2026-10 审查实测）。
 */

type Variant = 'primary' | 'secondary' | 'danger' | 'outline' | 'ghost' | 'soft'
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
    to?: RouteLocationRaw
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
  border-radius: var(--radius-md);
  font-family: inherit;
  text-decoration: none;
  cursor: pointer;
  /* 按钮上是控件名，不是正文，任何宽度下都不该折成两行——「创建账号」被挤成
     「建账 / 号」既认不出，也让同行按钮高度不齐。容器放不下时该由父级换行（flex-wrap）
     或让它溢出可见，而不是把名字断开。 */
  white-space: nowrap;
  transition:
    background-color var(--duration-fast) var(--ease-out-smooth),
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
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
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
}

.is-sm {
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.is-xs {
  gap: 6px;
  min-height: 28px;
  padding: 0;
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

/* 触屏下把两档小尺寸撑到可点高度（约 44px）。
   紧凑尺寸是为桌面鼠标的精度换来的密度，手指没有那个精度：28px 的行内按钮
   在手机上要么点不中、要么点到隔壁。只改触屏，桌面端的密集排布不受影响。
   md 本来就是 44px，不在这一段里。 */
@media (pointer: coarse) {
  .is-sm,
  .is-xs {
    min-height: var(--tap-target);
  }

  .is-icon-only.is-sm {
    width: var(--tap-target);
  }
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

.is-primary:hover:not(:disabled):not(.is-loading) {
  border-color: var(--accent-hover);
  background: var(--accent-hover);
  transform: translateY(-1px);
}

.is-primary:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.is-secondary {
  color: var(--text-secondary);
  background: var(--surface-sunken);
}

.is-secondary:hover:not(:disabled):not(.is-loading) {
  color: var(--text-primary);
  background: var(--surface-sunken-hover);
  transform: translateY(-1px);
}

.is-secondary:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.is-danger {
  color: var(--danger);
  background: var(--danger-soft);
}

.is-danger:hover:not(:disabled):not(.is-loading) {
  border-color: var(--danger);
  transform: translateY(-1px);
}

.is-danger:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.is-outline {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--surface-raised);
}

.is-outline:hover:not(:disabled):not(.is-loading) {
  border-color: var(--accent-hover);
  color: var(--text-on-accent);
  background: var(--accent-hover);
  transform: translateY(-1px);
}

.is-outline:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.is-ghost {
  color: var(--accent);
  background: transparent;
}

.is-ghost:hover:not(:disabled):not(.is-loading) {
  color: var(--accent-hover);
}

/* 当前项：浅强调底 + 强调字。悬停只换字色，不换底——已经选中的东西不该再浮起来。 */
.is-soft {
  color: var(--accent);
  background: var(--accent-soft);
}

.is-soft:hover:not(:disabled):not(.is-loading) {
  color: var(--accent-hover);
}

.is-ghost:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.is-soft:active:not(:disabled):not(.is-loading) {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

/* 禁用态换成实打实的「中性不可用」皮肤，而不是把变体色整体调透明。
 *
 * 原来只有一条 opacity: 0.55，它有两个毛病：一是主按钮变成「白字压在 55% 的松绿上」，
 * 实测对比度约 2.7:1，低于任何一档可读下限，观感更像渲染坏了而不像「点不了」
 * （登录页与设置中心的「保存」在表单没填时正好是这种状态，全站最容易被看到的两处）；
 * 二是所有变体一起变透明，反而看不出哪种是按钮了——secondary 本来就是下沉灰底，
 * 调透明之后与它的正常态几乎一模一样。
 *
 * 现在统一成：下沉底 + 细描边 + tertiary 字。细描边是这一版的关键，正是它把禁用态
 * 与「enabled 的 secondary」（同色底、无描边）分开；tertiary 是 tokens.css 里指派给
 * disabled 的那一档（浅色下约 4.1:1、深色下约 4.4:1，都还读得清），
 * 「不可用」靠描边与指针表达，不靠压低文字对比度。
 * 描边本来就存在（各变体都有 1px solid transparent），改成实色不会引起布局位移。
 * 选择器特异度 (0,3,0) 高于任一 .is-* 变体，且写在变体之后，两重保险都能压过它们。 */
.base-button:disabled:not(.is-loading) {
  border-color: var(--border-subtle);
  color: var(--text-tertiary);
  background: var(--surface-sunken);
  opacity: 1;
}

/* 加载中不压暗到禁用那么狠：内容仍要可读，也不能丢掉变体色——
   「正在做的事」还得认得出是哪个动作。 */
.is-loading {
  opacity: 0.82;
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
