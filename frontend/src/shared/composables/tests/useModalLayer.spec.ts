import { mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref, type Ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useModalLayer } from '../useModalLayer'

/* 模态层叠时的按键归属。
 *
 * 这条不是纸面需求：设置中心本身是一层浮层，它上面的「未保存草稿」确认框又是一层，
 * 而 Esc 的监听挂在 document 上——没有「只有最上面那层响应」这道判断，一次 Esc 会同时
 * 取消确认框并关掉设置中心。滚动锁的存取顺序同样只在叠层时才有意义，所以一起钉住。
 */

const wrappers: VueWrapper[] = []

function mountLayer(onEscape: () => void, open: Ref<boolean> = ref(true)): VueWrapper {
  const Host = defineComponent({
    setup() {
      const container = ref<HTMLElement | null>(null)
      useModalLayer({ open: () => open.value, container, onEscape })
      return () => h('div', { ref: container, tabindex: -1 }, 'layer')
    },
  })
  // attachTo：焦点与 document 上的监听都要求元素真的在文档里。
  const wrapper = mount(Host, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
}

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  document.body.replaceChildren()
  document.body.style.overflow = ''
})

describe('useModalLayer', () => {
  it('只有一层时，Esc 触发 onEscape', async () => {
    const onEscape = vi.fn()
    mountLayer(onEscape)
    await nextTick()

    pressEscape()

    expect(onEscape).toHaveBeenCalledOnce()
  })

  it('叠了两层时，Esc 只到最上面那层', async () => {
    const lower = vi.fn()
    const upper = vi.fn()
    mountLayer(lower)
    await nextTick()
    mountLayer(upper)
    await nextTick()

    pressEscape()

    expect(upper).toHaveBeenCalledOnce()
    expect(lower).not.toHaveBeenCalled()
  })

  it('上层关掉之后，Esc 才轮到底下那层', async () => {
    const lower = vi.fn()
    const upper = vi.fn()
    const upperOpen = ref(true)
    mountLayer(lower)
    await nextTick()
    mountLayer(upper, upperOpen)
    await nextTick()

    upperOpen.value = false
    await nextTick()
    pressEscape()

    expect(upper).not.toHaveBeenCalled()
    expect(lower).toHaveBeenCalledOnce()
  })

  it('叠层下的滚动锁按后进先出复位，body 不会留着 overflow: hidden', async () => {
    const lowerOpen = ref(true)
    const upperOpen = ref(true)
    mountLayer(vi.fn(), lowerOpen)
    await nextTick()
    mountLayer(vi.fn(), upperOpen)
    await nextTick()
    expect(document.body.style.overflow).toBe('hidden')

    upperOpen.value = false
    await nextTick()
    // 上层退出后，下层还在，背景仍该锁着。
    expect(document.body.style.overflow).toBe('hidden')

    lowerOpen.value = false
    await nextTick()
    expect(document.body.style.overflow).toBe('')
  })
})
