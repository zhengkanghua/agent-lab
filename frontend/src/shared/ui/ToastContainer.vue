<script setup lang="ts">
import {
  ToastProvider,
  ToastRoot,
  ToastTitle,
  ToastDescription,
  ToastClose,
  ToastViewport,
} from 'radix-vue'
import { X, CheckCircle, AlertTriangle, Info, XCircle } from '@lucide/vue'
import { useToast } from '@/shared/composables/useToast'

const { toasts, dismiss } = useToast()

const variantIcons = {
  info: Info,
  success: CheckCircle,
  warning: AlertTriangle,
  error: XCircle,
}
</script>

<template>
  <ToastProvider>
    <ToastRoot
      v-for="toast in toasts"
      :key="toast.id"
      :class="['toast', `toast--${toast.variant}`]"
      @update:open="(open) => !open && dismiss(toast.id)"
    >
      <component :is="variantIcons[toast.variant]" class="toast__icon" :size="20" />

      <div class="toast__content">
        <ToastTitle class="toast__title">{{ toast.title }}</ToastTitle>
        <ToastDescription v-if="toast.description" class="toast__description">
          {{ toast.description }}
        </ToastDescription>
      </div>

      <ToastClose class="toast__close" aria-label="关闭">
        <X :size="16" />
      </ToastClose>
    </ToastRoot>

    <ToastViewport class="toast-viewport" />
  </ToastProvider>
</template>

<style scoped>
.toast-viewport {
  position: fixed;
  top: 0;
  right: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  max-width: 420px;
  z-index: var(--z-toast);
}

.toast {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: var(--space-3);
  align-items: start;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  box-shadow: var(--shadow-soft);

  /* Inherit typography since ToastViewport mounts outside the main app container */
  font-family: var(--body-font);
  color: var(--text-primary);

  animation: slideInFromRight var(--duration-normal) var(--ease-out-smooth);
}

.toast[data-swipe='end'] {
  animation: slideOutToRight var(--duration-fast) var(--ease-out-smooth);
}

@keyframes slideInFromRight {
  from {
    opacity: 0;
    transform: translateX(100%);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes slideOutToRight {
  to {
    opacity: 0;
    transform: translateX(100%);
  }
}

.toast__icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.toast--info .toast__icon {
  color: var(--accent);
}
.toast--success .toast__icon {
  color: var(--success);
}
.toast--warning .toast__icon {
  color: var(--warning);
}
.toast--error .toast__icon {
  color: var(--danger);
}

.toast__content {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.toast__title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.toast__description {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
}

.toast__close {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out-smooth);
}

.toast__close:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}

.toast__close:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--accent-ring);
}

@media (prefers-reduced-motion: reduce) {
  .toast {
    animation: fadeIn var(--duration-fast);
  }
  .toast[data-swipe='end'] {
    animation: fadeOut var(--duration-fast);
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @keyframes fadeOut {
    to {
      opacity: 0;
    }
  }
}

@media (max-width: 640px) {
  .toast-viewport {
    left: 0;
    right: 0;
    padding: var(--space-3);
    max-width: none;
  }

  .toast {
    width: 100%;
  }
}
</style>
