import { onScopeDispose, watch, type Ref } from 'vue'
import { useFloatingPosition, type UseFloatingPositionReturn } from './useFloatingPosition'

/* 浮层（点一下才出现的小面板）的公共行为：点外关闭、Esc 关闭，外加定位与跟随。
 *
 * 收编前这套逻辑在两个选择器里各写了一份（shared/ui/KnowledgeBaseScopePicker.vue
 * 与 features/agent-chat/components/AgentModelPicker.vue）：都是原生 `<details>`，
 * 都自己挂 document 的 pointerdown 与 keydown 去补「点外面」和「Esc」两条退路，
 * 连注释都几乎逐字相同。两份的唯一差别是模型选择器多了个「打开时重拉目录」的事件。
 *
 * 比重复更麻烦的是两块都定位错了：`position: absolute; top: calc(100% + 4px)`
 * 写死在触发键下方，而触发键坐在贴底的输入坞里，面板于是整块展开到视口之外
 * （2026-10 实测：范围面板 71% 被切掉）。修在样式里只能各修一处、下次再有人
 * 写第三个浮层时照样踩。所以定位改成算出来的，翻不翻转由可用空间决定。
 *
 * 几何那一半在 useFloatingPosition 里（量尺寸、跟随滚动、面板长高重量），因为
 * 引用悬停卡（SafeMarkdown）要的是同一套几何、另一套开合策略。这一层只加
 * 「点一下才开、点外面或 Esc 才关」这一种策略。
 *
 * 为什么不用 useModalLayer：那不是模态。模态要锁背景滚动、要把 Tab 圈在面板里；
 * 这里是「点开一个下拉」，背景照常可滚、Tab 照常可以走出去。共用的只有「Esc 关闭」
 * 这一条，为它去借一套模态语义，代价是用户在下拉打开时滚不动页面。
 */

export interface UsePopoverPanelOptions {
  /** 面板是否打开。用 getter 而不是 ref，调用方可以直接传 computed。 */
  open: () => boolean
  /** 触发键元素。定位锚点，也是 Esc 关闭后焦点归还的目标。 */
  triggerRef: Ref<HTMLElement | null>
  /** 面板元素。需要在文档里才能量尺寸，所以由调用方在 v-if 渲染后绑定。 */
  panelRef: Ref<HTMLElement | null>
  /**
   * 用户要求关闭时回调（点面板外、按 Esc）。调用方据此把 open 置 false。
   * `reason` 用来区分要不要归还焦点：Esc 要，点外部不要。
   */
  onRequestClose: (reason: 'outside' | 'escape') => void
  /** 面板与触发键的间距，默认 6px。 */
  gap?: number
  /** 面板与视口边缘的最小间距，默认 8px。 */
  margin?: number
}

export function usePopoverPanel(options: UsePopoverPanelOptions): UseFloatingPositionReturn {
  const { position, positioned } = useFloatingPosition({
    open: options.open,
    anchorRef: options.triggerRef,
    panelRef: options.panelRef,
    gap: options.gap,
    margin: options.margin,
  })

  function onDocumentPointerDown(event: PointerEvent): void {
    const target = event.target as Node | null
    if (!target) return
    // 触发键上的按下不关：那一击接下来会走 click，由触发键自己翻转开合。
    // 少了这条，点触发键会先被这里关掉、再被 click 打开，面板永远关不上。
    if (options.triggerRef.value?.contains(target)) return
    if (options.panelRef.value?.contains(target)) return
    options.onRequestClose('outside')
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape') return
    options.onRequestClose('escape')
  }

  function attach(): void {
    document.addEventListener('pointerdown', onDocumentPointerDown)
    document.addEventListener('keydown', onKeydown)
  }

  function detach(): void {
    document.removeEventListener('pointerdown', onDocumentPointerDown)
    document.removeEventListener('keydown', onKeydown)
  }

  watch(
    () => options.open(),
    (isOpen) => (isOpen ? attach() : detach()),
    { immediate: true },
  )

  onScopeDispose(detach)

  return { position, positioned }
}
