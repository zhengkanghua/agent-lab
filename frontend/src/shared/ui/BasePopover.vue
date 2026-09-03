<script setup lang="ts">
import { PopoverRoot, PopoverTrigger, PopoverPortal, PopoverContent, PopoverArrow } from 'radix-vue'

/* 使用 Radix Vue 重写的 BasePopover，解决原有实现的问题：
 * 1. 自动碰撞检测和翻转（视口边缘不会被截断）
 * 2. 自动焦点管理和键盘导航
 * 3. 完整的 ARIA 属性支持
 * 4. Esc 关闭、点外部关闭
 */

interface Props {
  side?: 'top' | 'bottom' | 'left' | 'right'
  align?: 'start' | 'center' | 'end'
  sideOffset?: number
  arrow?: boolean
}

withDefaults(defineProps<Props>(), {
  side: 'bottom',
  align: 'start',
  sideOffset: 8,
  arrow: false,
})
</script>

<template>
  <PopoverRoot>
    <PopoverTrigger as-child>
      <slot name="trigger" />
    </PopoverTrigger>

    <PopoverPortal>
      <PopoverContent
        class="popover-content"
        :side="side"
        :align="align"
        :side-offset="sideOffset"
        :collision-padding="16"
      >
        <slot />
        <PopoverArrow v-if="arrow" class="popover-arrow" />
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>

<style>
/* 这个 style 块刻意不写 scoped：PopoverPortal 把弹层挂到 body，radix 渲染的
   content 元素拿不到本组件的 data-v 属性，scoped 规则一条都匹配不上——表现是
   弹层背景全透明、无层级，被 z-index:8 的检索输入坞整个盖住（2026-09 审查
   的「更多设置打不开」）。凡 Portal/Teleport 出组件树的内容，样式一律走全局。 */
.popover-content {
  /* Inherit typography since Portal mounts outside the main app container */
  font-family: var(--body-font);
  color: var(--text-primary);
  font-size: var(--text-base);

  /* Remove strict width limits to allow the consumer to size it naturally */
  padding: 13px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  z-index: var(--z-popover);

  /* 入场动画 */
  animation-duration: var(--duration-normal);
  animation-timing-function: var(--ease-out-smooth);
  animation-fill-mode: both;
}

.popover-content[data-side='top'] {
  animation-name: popover-slide-in-from-bottom;
}
.popover-content[data-side='bottom'] {
  animation-name: popover-slide-in-from-top;
}
.popover-content[data-side='left'] {
  animation-name: popover-slide-in-from-right;
}
.popover-content[data-side='right'] {
  animation-name: popover-slide-in-from-left;
}

@keyframes popover-slide-in-from-top {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes popover-slide-in-from-bottom {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes popover-slide-in-from-left {
  from {
    opacity: 0;
    transform: translateX(-8px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes popover-slide-in-from-right {
  from {
    opacity: 0;
    transform: translateX(8px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.popover-arrow {
  fill: var(--surface-raised);
}

/* radix 的定位 wrapper（position:fixed + transform）自身就是一个层叠上下文，
   .popover-content 上的 z-index 只在 wrapper 内部有效，对外真正起作用的层级
   只能写在 wrapper 身上；它带着内联 z-index:auto，所以要用 !important 压过。
   层级取 --z-popover：盖过输入坞与后台侧栏，让位于阅读层和 Toast。
   wrapper 是 radix 的稳定钩子属性，与 React Radix 生态同一写法。 */
div[data-radix-popper-content-wrapper] {
  z-index: var(--z-popover) !important;
}

@media (prefers-reduced-motion: reduce) {
  .popover-content {
    animation-name: popover-fade-in !important;
    animation-duration: var(--duration-fast) !important;
  }

  @keyframes popover-fade-in {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
}
</style>
