import { onScopeDispose, readonly, ref, watch, type Ref } from 'vue'

/* 贴底跟随 + 「回到最新」。Agent 对话页与检索页共用。
 *
 * 为什么需要它：这两页的内容都是往下长的（回答一轮轮追加、检索记录一条条累积），
 * 而滚动容器都是文档本身——两页的 .workspace 只有 min-height，没有自己的滚动条。
 *
 * 它替换掉了 Agent 页原来那两条无条件滚到底的 watch（轮数变化时、以及「正在生成 / 正在重连」
 * 状态行出现时）。那两条有两个后果：流式回答越长，尾部越是被贴底的输入坞挡在视口外，没人把它
 * 带回来；而用户往上翻看历史时，任何一次轮数变化都会把视口拽走。
 *
 * 现在的规则按「谁发起了这次移动」分开：
 *   1. 内容长高（流式 token、新增轮次、状态行出现、检索结果落地）只在**已经贴底**时跟随。
 *      用户上翻了就由他自己决定什么时候回去，ScrollToBottomButton 负责给这个入口；
 *   2. 「打开会话」「提交检索」这类**用户主动要求看新的**动作，由调用方直接调
 *      scrollToBottom()，不由本组合式函数猜。
 *
 * 判据是「我们上次把视口钉在哪」（pinnedScrollY），不是「当前离底部多远」。
 * 两者在正常情况下等价，但后者有一个会自我中断的竞态：跟随滚动会派发一次 scroll 事件，
 * 那一刻文档往往又长高了一截，于是「离底部 300px」被读成「用户往上翻了」——跟随就此停住，
 * 而且再也没有事件把它叫醒（2026-10 实测：连问 8 轮后视口停在距底 984px、按钮常亮）。
 * 内容长高不会改变滚动位置，用户上翻才会，所以拿钉住位置比才分得清这两件事。
 *
 * 观察高度用 ResizeObserver 而不是盯每个 token：只要内容长高就跟着走，不必知道是哪一种变化
 * 引起的，新增状态行、错误面板、工具轨迹展开都自动覆盖。
 */

/** 比钉住处高出这么多才算「用户往上翻了」：留余量，避免缩放与高分屏下的 1px 误差。 */
const BOTTOM_THRESHOLD_PX = 48

/**
 * @param content 观察高度变化的元素。它长高就说明有新内容；传 null 时不观察（例如首屏还没挂上）。
 */
export function useStickToBottom(content: Ref<HTMLElement | null>) {
  /** 视口是否贴在底部。初始值在挂上观察对象时按真实滚动位置算。 */
  const atBottom = ref(true)
  /** 用户上翻期间又来了新内容；按钮据此从「回到最新」变成「有新的内容」。 */
  const hasNewContent = ref(false)

  let observed: HTMLElement | null = null
  let observer: ResizeObserver | null = null
  /** 上一次把视口钉住的位置（滚动坐标）。用户滚到这个位置附近就算还在底部。 */
  let pinnedScrollY = 0

  function maxScrollY(): number {
    return Math.max(0, document.documentElement.scrollHeight - window.innerHeight)
  }

  /** 把视口钉到文档底部，并记下钉住的位置。 */
  function pinToBottom(behavior: ScrollBehavior = 'auto'): void {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior })
    pinnedScrollY = maxScrollY()
    atBottom.value = true
    hasNewContent.value = false
  }

  function syncFromScroll(): void {
    const reached = window.scrollY >= pinnedScrollY - BOTTOM_THRESHOLD_PX
    atBottom.value = reached
    if (reached) hasNewContent.value = false
  }

  function onContentResize(): void {
    // 不在这里重新算位置：回调跑在布局之后，此刻高度已经长上去了，按距离判必然得出
    // 「不在底部」，于是最该跟随的时候反而不跟随。贴底与否由滚动位置说了算。
    if (atBottom.value) {
      pinToBottom()
      return
    }
    hasNewContent.value = true
  }

  function onViewportResize(): void {
    // 视口变高会把可滚范围压小、浏览器随之夹住 scrollY。钉住的位置要跟着一起收，
    // 否则一次窗口缩放就会被读成「用户往上翻了」。
    const pinnedWasBottom = atBottom.value
    pinnedScrollY = Math.min(pinnedScrollY, maxScrollY())
    if (pinnedWasBottom) pinToBottom()
    else syncFromScroll()
  }

  function detach(): void {
    observer?.disconnect()
    observer = null
    observed = null
    window.removeEventListener('scroll', syncFromScroll)
    window.removeEventListener('resize', onViewportResize)
  }

  watch(
    content,
    (element) => {
      if (element === observed) return
      detach()
      if (!element) return

      observed = element
      observer = new ResizeObserver(onContentResize)
      observer.observe(element)
      window.addEventListener('scroll', syncFromScroll, { passive: true })
      window.addEventListener('resize', onViewportResize)
      // 首屏按真实滚动位置定一次：刚进页面在顶部、文档又很长时，不该算作「贴在底部」
      // （长会话的首屏落点由调用方显式 scrollToBottom 决定，不靠这里瞎猜）。
      pinnedScrollY = maxScrollY()
      syncFromScroll()
    },
    { immediate: true, flush: 'post' },
  )

  onScopeDispose(detach)

  return {
    atBottom: readonly(atBottom),
    hasNewContent: readonly(hasNewContent),
    scrollToBottom: pinToBottom,
    /** 外部整段替换内容后手动对一次状态（例如回放替换了整段历史）。 */
    syncAtBottom: syncFromScroll,
  }
}
