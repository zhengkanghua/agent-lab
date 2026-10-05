import { nextTick, onScopeDispose, readonly, ref, watch, type Ref } from 'vue'
import {
  computePopoverPosition,
  type PopoverAlign,
  type PopoverPosition,
  type PopoverPrefer,
} from '@/shared/model/popover-position'

/* 浮层几何：量触发元素与面板、算出落点、并在它们动的时候跟住。
 *
 * 这是「浮层」这件事里唯一需要 DOM 的一段，从 usePopoverPanel 里分出来是因为
 * 开合策略有两种、几何只有一套：
 *   - 下拉面板（usePopoverPanel）：点触发键开合，点外面 / Esc 关闭；
 *   - 引用悬停卡（SafeMarkdown）：悬停或聚焦时出现，移开就消失，没有 Esc 也没有点外面。
 * 两者的共同点只有「面板该落在哪、动了要不要跟上」，分开之后各自那份策略不用迁就对方。
 *
 * 面板必须是 position: fixed 且挂在 body 上的（由调用方 Teleport），这样包含块就是视口，
 * getBoundingClientRect() 量到的坐标能直接当 top / left 用，不用换算 offsetParent 链；
 * 也因此不会被触发元素那些 `overflow` 祖先裁掉。
 */

export interface UseFloatingPositionOptions {
  /** 面板是否可见。用 getter 而不是 ref，调用方可以直接传 computed。 */
  open: () => boolean
  /** 触发元素。定位锚点。 */
  anchorRef: Ref<HTMLElement | null>
  /** 面板元素。需要在文档里才能量尺寸，所以由调用方在 v-if 渲染后绑定。 */
  panelRef: Ref<HTMLElement | null>
  /** 面板与触发元素的间距，默认 6px。 */
  gap?: number
  /** 面板与视口边缘的最小间距，默认 8px。 */
  margin?: number
  /** 优先展开到哪一侧，默认 below。 */
  prefer?: PopoverPrefer
  /** 水平对齐，默认 start（与触发元素左对齐）。 */
  align?: PopoverAlign
}

export interface UseFloatingPositionReturn {
  /** 面板落点；`positioned` 为 false 时这个值还没量过，不该画出来。 */
  position: Readonly<Ref<PopoverPosition>>
  /** 是否已经量过一次。面板首帧拿到的是 (0,0)，少了这道判断会在左上角闪一下。 */
  positioned: Readonly<Ref<boolean>>
}

export function useFloatingPosition(
  options: UseFloatingPositionOptions,
): UseFloatingPositionReturn {
  const gap = options.gap ?? 6
  const margin = options.margin ?? 8

  const position = ref<PopoverPosition>({ top: 0, left: 0 })
  const positioned = ref(false)

  function reposition(): void {
    const anchor = options.anchorRef.value
    const panel = options.panelRef.value
    if (!anchor || !panel) return
    const anchorRect = anchor.getBoundingClientRect()
    const panelRect = panel.getBoundingClientRect()
    position.value = computePopoverPosition({
      trigger: {
        top: anchorRect.top,
        bottom: anchorRect.bottom,
        left: anchorRect.left,
        right: anchorRect.right,
      },
      panel: { width: panelRect.width, height: panelRect.height },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      gap,
      margin,
      prefer: options.prefer,
      align: options.align,
    })
    positioned.value = true
  }

  let observer: ResizeObserver | null = null

  function attach(): void {
    window.addEventListener('resize', reposition)
    // capture: 面板贴的是触发元素，页面里任何一个滚动容器动一步它都得跟上，
    // 而 scroll 事件不冒泡——只在 window 上监听收不到内层容器的滚动。
    window.addEventListener('scroll', reposition, true)
    /* 面板自己长高也要重量：范围面板从「所有知识库」切到「指定知识库」会多出一列，
       模型面板从「加载中」变成有结果也一样。不重量的话，向上展开的面板会朝头顶长出去。 */
    if (typeof ResizeObserver === 'function' && options.panelRef.value) {
      observer = new ResizeObserver(reposition)
      observer.observe(options.panelRef.value)
    }
  }

  function detach(): void {
    window.removeEventListener('resize', reposition)
    window.removeEventListener('scroll', reposition, true)
    observer?.disconnect()
    observer = null
  }

  watch(
    () => options.open(),
    async (isOpen) => {
      if (!isOpen) {
        detach()
        positioned.value = false
        return
      }
      // 面板由调用方的 v-if 控制，同一 tick 里 panelRef 还是 null，量不到尺寸。
      // nextTick 在浏览器绘制之前跑完，所以首帧就是算好的位置，不会先在左上角闪一下。
      await nextTick()
      if (!options.open()) return
      reposition()
      attach()
    },
    { immediate: true },
  )

  onScopeDispose(detach)

  return { position: readonly(position), positioned: readonly(positioned) }
}
