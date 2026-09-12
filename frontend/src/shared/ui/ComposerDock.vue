<script setup lang="ts">
import { computed, useSlots } from 'vue'

/* 输入坞：检索与 Agent 共用的输入区视觉外壳（2026-09 重设计 P2）。
 *
 * 只负责「一个输入区长得像什么」：24px 大圆角、浮起的白面、聚焦时描边亮出
 * 强调色、底部一条左右分栏的操作栏。输入行为（自动长高、Enter 提交、校验）
 * 全部归使用方，本组件一个行为 prop 都不接。
 *
 * 与页面同名类的分工：SearchPage 里负责吸附定位的容器也叫 .composer-dock，
 * 那是页面 scoped 的布局类（sticky）；这里是组件内的视觉坞（圆角/描边/阴影）。
 * 两处都是 scoped 样式，互不冲突，改名反而会丢掉这层对照关系。
 *
 * 提交键的位置约定：type="submit" 的按钮要放进 bar-right 时，坞必须整个被
 * 使用方的 <form> 包住——按钮不在表单内的话，点击不会触发提交。
 */

const slots = useSlots()

/* 不叫 ariaLabel：vue-tsc 把 aria-* 保留给原生 attribute，不映射到同名 prop。 */
defineProps<{ label: string }>()

const hasBar = computed(() => Boolean(slots['bar-left'] || slots['bar-right']))
</script>

<template>
  <section class="composer-dock" :aria-label="label" style="container-type: inline-size">
    <div class="dock-body">
      <slot />
    </div>
    <div v-if="hasBar" class="dock-bar">
      <div class="dock-bar-left"><slot name="bar-left" /></div>
      <div class="dock-bar-right"><slot name="bar-right" /></div>
    </div>
  </section>
</template>

<style scoped>
.composer-dock {
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-xl);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth);
}

/* 焦点环画在坞框上：视觉上这一整块是一个输入控件，控件内部不再各自描边。 */
.composer-dock:focus-within {
  border-color: var(--accent);
  box-shadow:
    0 0 0 4px var(--accent-soft),
    var(--shadow-soft);
}

/* 大块背景刻意不配 background transition：主题切换的瞬间整页底色瞬时翻转，
   唯独还在渐变的它会留下一块旧底矩形（老板录屏实测，交接文档 §五-4）。 */

.dock-body {
  padding: 12px 16px 4px;
}

.dock-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 12px 10px;
}

.dock-bar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
}

.dock-bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
</style>
