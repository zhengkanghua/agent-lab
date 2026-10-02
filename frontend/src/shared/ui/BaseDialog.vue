<script setup lang="ts">
import { ref } from 'vue'
import { useModalLayer } from '@/shared/composables/useModalLayer'

/* 居中对话框（2026-09 重设计 P4 的「管理桌面部件」）。
 *
 * 形态：居中 520px、16 圆角、42% 遮罩（--surface-overlay）。Esc 与表单自己的
 * 关闭键都是出口；遮罩点击不关——后台的表单多是创建/编辑，误触遮罩丢一整张
 * 草稿的代价太高。焦点约束（Tab 循环）、滚动锁、打开时聚焦面板、关闭后焦点归还
 * 触发点，都由 useModalLayer 承担（2026-10 收编，此前这套逻辑在五个地方各写一份）。
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

useModalLayer({
  open: () => props.open,
  container: panelRef,
  onEscape: () => emit('close'),
})
</script>

<template>
  <div v-if="open" class="base-dialog-overlay">
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
