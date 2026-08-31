<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

/* 锚定在触发元素旁边的浮层。三条行为按需求定死：Esc 关闭、点外部关闭、
 * 关闭后焦点回到触发元素。
 *
 * 触发元素由本组件通过 trigger 插槽持有，不交给调用方自己放在外面。
 * 原因是「点外部关闭」必须能认出触发元素本身：否则点齿轮按钮时，
 * document 上的关闭和按钮上的切换会同时发生，一开一关抵消成没反应。
 *
 * 和 DocumentReader 的区别是这里不做焦点陷阱——浮层不是模态，
 * Tab 应该能走出去。所以只有 Esc 与主动关闭会归还焦点，
 * 点外部不归还：那时用户已经把注意力放到别处了，抢回来是打扰。
 *
 * 定位用 absolute + placement，没有引入 floating-ui，所以不具备
 * 碰到视口边缘自动翻转的能力。调用方要自己选对方向（贴底的输入区用 top-*）。
 * 另外浮层留在原地不 Teleport，祖先出现 overflow: hidden 会裁掉它。
 */

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 读屏用的浮层名称。浮层是 role="dialog"，没有名字读屏只会念「对话框」。 */
    label: string
    placement?: 'top-start' | 'top-end' | 'bottom-start' | 'bottom-end'
  }>(),
  { placement: 'bottom-start' },
)

const emit = defineEmits<{ 'update:open': [boolean] }>()

const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const triggerWrap = ref<HTMLElement | null>(null)

const generatedId = useId()
const panelId = computed(() => `popover-${generatedId}`)

const triggerAttrs = computed(() => ({
  'aria-haspopup': 'dialog' as const,
  'aria-expanded': props.open,
  'aria-controls': props.open ? panelId.value : undefined,
}))

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

watch(
  () => props.open,
  async (open) => {
    if (open) {
      document.addEventListener('keydown', onKeydown)
      document.addEventListener('pointerdown', onPointerDown)
      await nextTick()
      // 浮层里通常第一个就是要填的输入框；一个都没有就退回聚焦浮层本身，
      // 这样 Esc 才有地方接收。
      const first = panel.value?.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? panel.value)?.focus()
      return
    }
    releaseListeners()
  },
  /* immediate 不能省：调用方可能一挂载就是展开的（比如从路由状态恢复），
     少了它那种浮层没有任何监听，Esc 和点外部都关不掉。 */
  { immediate: true },
)

onBeforeUnmount(releaseListeners)

function releaseListeners(): void {
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('pointerdown', onPointerDown)
}

function close(restoreFocus: boolean): void {
  if (!props.open) return
  emit('update:open', false)
  if (!restoreFocus) return
  // 先等浮层从 DOM 里撤掉再聚焦，否则焦点还在将被移除的节点上，
  // 浏览器会把它丢回 body。
  void nextTick(() => {
    triggerWrap.value?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
  })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  event.preventDefault()
  close(true)
}

function onPointerDown(event: PointerEvent): void {
  const target = event.target
  if (!(target instanceof Node)) return
  if (root.value?.contains(target)) return
  close(false)
}

function toggle(): void {
  if (props.open) {
    close(true)
    return
  }
  emit('update:open', true)
}
</script>

<template>
  <div ref="root" class="base-popover">
    <div ref="triggerWrap" class="popover-trigger">
      <slot name="trigger" :toggle="toggle" :open="open" :attrs="triggerAttrs" />
    </div>
    <div
      v-if="open"
      :id="panelId"
      ref="panel"
      class="popover-panel"
      :class="`is-${placement}`"
      role="dialog"
      :aria-label="label"
      tabindex="-1"
    >
      <slot :close="() => close(true)" />
    </div>
  </div>
</template>

<style scoped>
.base-popover {
  position: relative;
  display: inline-flex;
}

.popover-trigger {
  display: inline-flex;
}

.popover-panel {
  position: absolute;
  z-index: 40;
  width: max-content;
  max-width: min(380px, calc(100vw - 32px));
  padding: 13px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
}

.popover-panel:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.is-top-start,
.is-top-end {
  bottom: calc(100% + 8px);
}

.is-bottom-start,
.is-bottom-end {
  top: calc(100% + 8px);
}

.is-top-start,
.is-bottom-start {
  left: 0;
}

.is-top-end,
.is-bottom-end {
  right: 0;
}
</style>
