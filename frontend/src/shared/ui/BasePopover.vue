<script setup lang="ts">
import {
  PopoverRoot,
  PopoverTrigger,
  PopoverPortal,
  PopoverContent,
  PopoverArrow,
} from 'radix-vue'

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

<style scoped>
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
  z-index: 50;

  /* 入场动画 */
  animation-duration: var(--duration-normal);
  animation-timing-function: var(--ease-out-smooth);
  animation-fill-mode: both;
}

.popover-content[data-side='top'] {
  animation-name: slideInFromBottom;
}
.popover-content[data-side='bottom'] {
  animation-name: slideInFromTop;
}
.popover-content[data-side='left'] {
  animation-name: slideInFromRight;
}
.popover-content[data-side='right'] {
  animation-name: slideInFromLeft;
}

@keyframes slideInFromTop {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes slideInFromBottom {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes slideInFromLeft {
  from {
    opacity: 0;
    transform: translateX(-8px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes slideInFromRight {
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

@media (prefers-reduced-motion: reduce) {
  .popover-content {
    animation-name: fadeIn !important;
    animation-duration: var(--duration-fast) !important;
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
}
</style>
