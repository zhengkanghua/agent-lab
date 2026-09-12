<script setup lang="ts">
import { nextTick, onScopeDispose, ref, watch } from 'vue'

/* 居中对话框（2026-09 重设计 P4 的「管理桌面部件」）。
 *
 * 形态：居中 520px、16 圆角、42% 遮罩（--surface-overlay）。Esc 与表单自己的
 * 关闭键都是出口；遮罩点击不关——后台的表单多是创建/编辑，误触遮罩丢一整张
 * 草稿的代价太高。焦点约束（Tab 循环）、打开时聚焦面板、关闭后焦点归还触发点，
 * 都由本组件全包，使用方只管 open / label / @close。
 *
 * 不用 Teleport：fixed 定位 + --z-overlay 已盖过后台一切内容（顶栏 20、抽屉 40），
 * 留在原地反而让测试与 SSR 不用穿透。
 */

const props = defineProps<{
  open: boolean
  /** 读屏播报的对话框名；可见标题由使用方放在内容里。 */
  label: string
}>()

const emit = defineEmits<{ close: [] }>()

const panelRef = ref<HTMLElement | null>(null)
let returnFocusTo: HTMLElement | null = null
let previousBodyOverflow: string | null = null

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const focusables = panelRef.value?.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusables || focusables.length === 0) return
  const first = focusables[0]!
  const last = focusables[focusables.length - 1]!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function release(): void {
  if (previousBodyOverflow !== null) {
    document.body.style.overflow = previousBodyOverflow
    previousBodyOverflow = null
  }
  if (returnFocusTo) {
    returnFocusTo.focus()
    returnFocusTo = null
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      returnFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
      previousBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      await nextTick()
      if (props.open) panelRef.value?.focus()
    } else {
      release()
    }
  },
)

onScopeDispose(release)
</script>

<template>
  <div v-if="open" class="base-dialog-overlay" @keydown="onKeydown">
    <section
      ref="panelRef"
      class="base-dialog"
      role="dialog"
      aria-modal="true"
      :aria-label="label"
      tabindex="-1"
    >
      <slot />
    </section>
  </div>
</template>

<style scoped>
.base-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-overlay);
  display: grid;
  place-items: center;
  padding: var(--space-6);
  background: var(--surface-overlay);
}

.base-dialog {
  display: flex;
  flex-direction: column;
  width: min(520px, 100%);
  max-height: calc(100dvh - var(--space-6) * 2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  overflow: hidden;
}

/* 焦点由面板容器持有（tabindex=-1），容器自己不画环：面板整体不是控件。 */
.base-dialog:focus {
  outline: none;
}

/* 内容第一块自带的上边距会让面板头顶出一条空隙，压掉。 */
.base-dialog > :deep(*:first-child) {
  margin-top: 0;
}
</style>
