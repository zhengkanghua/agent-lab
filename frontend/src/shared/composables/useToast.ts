import { ref } from 'vue'

export type ToastVariant = 'info' | 'success' | 'warning' | 'error'

export interface Toast {
  id: string
  title: string
  description?: string
  variant: ToastVariant
  duration?: number
}

const toasts = ref<Toast[]>([])
let idCounter = 0

export function useToast() {
  function show(
    title: string,
    options?: {
      description?: string
      variant?: ToastVariant
      duration?: number
    },
  ) {
    const id = `toast-${++idCounter}`
    const duration = options?.duration ?? 3000
    const toast: Toast = {
      id,
      title,
      description: options?.description,
      variant: options?.variant ?? 'info',
      duration,
    }

    toasts.value.push(toast)

    if (duration > 0) {
      setTimeout(() => {
        dismiss(id)
      }, duration)
    }
  }

  function dismiss(id: string) {
    const index = toasts.value.findIndex((t) => t.id === id)
    if (index > -1) {
      toasts.value.splice(index, 1)
    }
  }

  function success(title: string, description?: string) {
    show(title, { description, variant: 'success' })
  }

  function error(title: string, description?: string) {
    show(title, { description, variant: 'error' })
  }

  function warning(title: string, description?: string) {
    show(title, { description, variant: 'warning' })
  }

  function info(title: string, description?: string) {
    show(title, { description, variant: 'info' })
  }

  return {
    toasts,
    show,
    dismiss,
    success,
    error,
    warning,
    info,
  }
}
