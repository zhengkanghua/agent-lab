import { nextTick, onScopeDispose, watch, type Ref } from 'vue'

/* 模态层（对话框 / 抽屉 / 阅读层）的公共行为：滚动锁、Esc、Tab 焦点循环、
 * 打开时给初始焦点、关闭后归还焦点。
 *
 * 收编前这套逻辑被手写了五遍：BaseDialog、AppShell 与 AdminShell 的抽屉
 * （这两份逐字相同）、SettingsPage 的浮层、DocumentReader 的阅读层。五份的行为
 * 还各不相同——有的压根不锁背景滚动，有的把 Tab 循环挂在面板上（焦点一旦跑到
 * 面板外，循环就静默失效），可聚焦元素的选择器也有三个版本。而「模态」这件事
 * 的正确答案只有一套。
 *
 * 监听挂在 document 上而不是容器上：模态打开时焦点可能在容器外（浏览器把焦点
 * 挪到 body、或用户点了遮罩），挂在容器上时那些情况一律收不到 Esc。挂在 document
 * 上则无论焦点在哪都收得到。
 */

/** 容器内可参与 Tab 顺序的元素。与 BaseDialog 原有的选择器一致，收编时取最全的一份。 */
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export interface ModalLayerOptions {
  /** 是否处于打开态。用 getter 而不是 ref，让调用方可以直接传一个 computed。 */
  open: () => boolean
  /** 焦点被约束在其中的容器，需带 tabindex="-1" 才能在无内部控件时接住焦点。 */
  container: Ref<HTMLElement | null>
  /** 按下 Esc 时执行；省略则不接管 Esc（窄屏整页形态就不该接管）。 */
  onEscape?: () => void
  /** 打开后把焦点放到哪个元素；省略则聚焦容器本身。 */
  initialFocus?: () => HTMLElement | null | undefined
  /** 关闭后把焦点还给谁；省略则还给打开那一刻持焦点的元素。 */
  restoreFocus?: () => HTMLElement | null | undefined
  /** 打开时锁背景滚动，默认 true。 */
  lockScroll?: boolean
  /** 把 Tab 焦点限制在容器内，默认 true。 */
  trapTab?: boolean
}

export function useModalLayer(options: ModalLayerOptions): void {
  let active = false
  let previousBodyOverflow = ''
  let returnFocusTo: HTMLElement | null = null

  const locksScroll = options.lockScroll !== false

  function focusableItems(): HTMLElement[] {
    const root = options.container.value
    if (!root) return []
    return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      // hidden 属性的元素拿不到焦点，留在列表里会让首尾判定落到一个点不中的元素上。
      (element) => !element.hasAttribute('hidden'),
    )
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && options.onEscape) {
      event.preventDefault()
      options.onEscape()
      return
    }
    if (event.key !== 'Tab' || options.trapTab === false) return

    const items = focusableItems()
    if (items.length === 0) {
      event.preventDefault()
      options.container.value?.focus()
      return
    }

    const first = items[0]!
    const last = items[items.length - 1]!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  function activate(): void {
    if (active) return
    active = true
    returnFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (locksScroll) {
      previousBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
    document.addEventListener('keydown', onKeydown)
  }

  function release(): void {
    if (!active) return
    active = false
    document.removeEventListener('keydown', onKeydown)
    if (locksScroll) {
      document.body.style.overflow = previousBodyOverflow
      previousBodyOverflow = ''
    }
    const target = options.restoreFocus ? options.restoreFocus() : returnFocusTo
    returnFocusTo = null
    target?.focus()
  }

  watch(
    () => options.open(),
    async (isOpen) => {
      if (!isOpen) {
        release()
        return
      }
      activate()
      // 等容器与它的内容渲染完再送焦点：容器多半由 v-if 控制，同一 tick 里还是 null。
      await nextTick()
      if (!active) return
      const initial = options.initialFocus?.() ?? options.container.value
      initial?.focus()
    },
    { immediate: true },
  )

  onScopeDispose(release)
}
