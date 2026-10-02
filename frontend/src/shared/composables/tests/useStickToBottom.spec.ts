import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStickToBottom } from '../useStickToBottom'
import { ResizeObserverStub, resetResizeObservers } from '@/test-support/browser-apis'

/* 跟随逻辑的核验。
 *
 * jsdom 里没有真实布局，所以不用「元素真的长高」来驱动，而是自己安排滚动几何
 * （scrollHeight / scrollY / innerHeight）再手动触发 ResizeObserver 的回调——
 * 判定依据是这三个数，与真实浏览器一致。
 */

type StickApi = ReturnType<typeof useStickToBottom>

/** 安排文档的滚动几何。distance = scrollHeight - scrollY - innerHeight。 */
function geometry({
  scrollHeight,
  scrollY,
  innerHeight,
}: {
  scrollHeight: number
  scrollY: number
  innerHeight: number
}): void {
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    configurable: true,
    value: scrollHeight,
  })
  Object.defineProperty(window, 'scrollY', { configurable: true, value: scrollY })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: innerHeight })
}

function observer(): ResizeObserverStub {
  const instance = ResizeObserverStub.instances[0]
  if (!instance) throw new Error('没有观察到 ResizeObserver 实例')
  return instance
}

async function mountHost(): Promise<{ api: StickApi; unmount: () => void }> {
  let api!: StickApi
  const Host = defineComponent({
    setup() {
      const content = ref<HTMLElement | null>(null)
      api = useStickToBottom(content)
      return () => h('div', { ref: content })
    },
  })
  const wrapper = mount(Host)
  // watch 是 flush: 'post'，元素 ref 要等这一拍之后才拿得到。
  await nextTick()
  return { api, unmount: () => wrapper.unmount() }
}

const scrollTo = vi.fn()

beforeEach(() => {
  resetResizeObservers()
  geometry({ scrollHeight: 900, scrollY: 0, innerHeight: 900 })
  vi.stubGlobal('scrollTo', scrollTo)
})

afterEach(() => {
  vi.unstubAllGlobals()
  scrollTo.mockReset()
})

describe('useStickToBottom', () => {
  it('挂上就观察内容元素，卸载时断开', async () => {
    const { unmount } = await mountHost()
    expect(ResizeObserverStub.instances).toHaveLength(1)
    expect(observer().targets.size).toBe(1)

    unmount()
    expect(ResizeObserverStub.instances).toHaveLength(0)
  })

  it('已经贴底时，内容长高就跟着滚到底', async () => {
    // 距离底部 0：视为贴底。
    geometry({ scrollHeight: 900, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()
    expect(api.atBottom.value).toBe(true)

    // 内容长高：滚动位置没变，但文档变长了。
    geometry({ scrollHeight: 1800, scrollY: 0, innerHeight: 900 })
    observer().trigger()

    expect(scrollTo).toHaveBeenCalledWith({ top: 1800, behavior: 'auto' })
    expect(api.hasNewContent.value).toBe(false)
  })

  it('跟随自己引发的那次 scroll 事件，不会被误判成用户上翻', async () => {
    /* 这条钉住 2026-10 实测到的那次自我中断：跟随滚动会派发 scroll，而那一刻文档
       往往又长高了一截。若按「当前离底部多远」判断，就会读成「用户上翻了」，
       跟随当场停住且再无事件唤醒。 */
    geometry({ scrollHeight: 900, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()

    // 内容长高 → 跟随，钉在 2000 - 900 = 1100 处。
    geometry({ scrollHeight: 2000, scrollY: 0, innerHeight: 900 })
    observer().trigger()
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })

    // 浏览器把视口落到夹住后的位置，随后派发 scroll；此刻文档又长高到 2400。
    geometry({ scrollHeight: 2400, scrollY: 1100, innerHeight: 900 })
    window.dispatchEvent(new Event('scroll'))

    expect(api.atBottom.value).toBe(true)
    expect(api.hasNewContent.value).toBe(false)
  })

  it('用户从底部往上拖动：立刻停止跟随', async () => {
    geometry({ scrollHeight: 2000, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()
    geometry({ scrollHeight: 2000, scrollY: 1100, innerHeight: 900 })
    window.dispatchEvent(new Event('scroll'))
    expect(api.atBottom.value).toBe(true)

    geometry({ scrollHeight: 2000, scrollY: 500, innerHeight: 900 })
    window.dispatchEvent(new Event('scroll'))

    expect(api.atBottom.value).toBe(false)
  })

  it('用户上翻之后内容长高：不滚动，只亮出「有新内容」', async () => {
    // 距离底部 1200：远在底部之外。
    geometry({ scrollHeight: 2100, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()
    expect(api.atBottom.value).toBe(false)

    geometry({ scrollHeight: 2400, scrollY: 0, innerHeight: 900 })
    observer().trigger()

    expect(scrollTo).not.toHaveBeenCalled()
    expect(api.hasNewContent.value).toBe(true)
  })

  it('往下滚回底部：恢复跟随并清掉「有新内容」', async () => {
    geometry({ scrollHeight: 2100, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()
    observer().trigger()
    expect(api.hasNewContent.value).toBe(true)

    geometry({ scrollHeight: 2400, scrollY: 1500, innerHeight: 900 })
    window.dispatchEvent(new Event('scroll'))

    expect(api.atBottom.value).toBe(true)
    expect(api.hasNewContent.value).toBe(false)
  })

  it('scrollToBottom 落到文档底部并复位两个状态', async () => {
    geometry({ scrollHeight: 2400, scrollY: 0, innerHeight: 900 })
    const { api } = await mountHost()
    observer().trigger()
    expect(api.hasNewContent.value).toBe(true)

    api.scrollToBottom('smooth')

    expect(scrollTo).toHaveBeenCalledWith({ top: 2400, behavior: 'smooth' })
    expect(api.atBottom.value).toBe(true)
    expect(api.hasNewContent.value).toBe(false)
  })

  it('容差内的微小距离仍算贴底：1px 误差不该把跟随关掉', async () => {
    geometry({ scrollHeight: 1800, scrollY: 860, innerHeight: 900 })
    const { api } = await mountHost()

    expect(api.atBottom.value).toBe(true)
  })
})
