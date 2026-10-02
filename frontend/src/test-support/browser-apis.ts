/* 补 jsdom 缺的浏览器 API。
 *
 * 补在测试环境层，不在生产代码里写「有没有这个全局」的分支：那种兜底会让浏览器里真正该走的
 * 路径在测试中被静默跳过。目前缺两个，都是生产代码正当依赖的标准 API：
 *   - ResizeObserver：useStickToBottom 靠它跟随内容长高；
 *   - matchMedia：Agent 页判断是不是触屏（触屏下不自动聚焦，免得弹软键盘）。
 *
 * 本模块被 vitest.config.ts 列为 setupFiles，import 时就装上；测试需要拿到替身实例时
 * 直接从这个路径 import，不必再复制一份。
 */

/* ---------- ResizeObserver ---------- */

/* 替身只做两件事：记录「谁在观察什么」、允许手动触发一次回调。它不测量真实尺寸——
 * jsdom 里元素高度全是 0，测不出增长。要核验跟随行为的测试拿 instances 找到实例再 trigger()。 */

type ResizeCallback = (entries: ResizeObserverEntry[], observer: ResizeObserver) => void

export class ResizeObserverStub {
  /** 当前活着的实例。用例之间用 resetResizeObservers() 清一次。 */
  static instances: ResizeObserverStub[] = []

  readonly callback: ResizeCallback
  readonly targets = new Set<Element>()

  constructor(callback: ResizeCallback) {
    this.callback = callback
    ResizeObserverStub.instances.push(this)
  }

  observe(target: Element): void {
    this.targets.add(target)
  }

  unobserve(target: Element): void {
    this.targets.delete(target)
  }

  disconnect(): void {
    this.targets.clear()
    const index = ResizeObserverStub.instances.indexOf(this)
    if (index >= 0) ResizeObserverStub.instances.splice(index, 1)
  }

  /** 测试用：模拟「被观察的内容长高了一次」。 */
  trigger(): void {
    this.callback([], this as unknown as ResizeObserver)
  }
}

export function resetResizeObservers(): void {
  ResizeObserverStub.instances = []
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

/* ---------- matchMedia ---------- */

/* jsdom 没有 matchMedia。替身一律回答「不匹配」：媒体查询的判定结果属于真实浏览器，
 * 测试里假装成某个指针类型只会让「触屏下不自动聚焦」这条分支以假前提通过。
 * 需要覆盖那条分支的测试自己 stub window.matchMedia。 */
if (typeof globalThis.matchMedia !== 'function') {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof globalThis.matchMedia
}
