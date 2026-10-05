import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import BasePopover from '@/shared/ui/BasePopover.vue'

/* 浮层的行为：开合的三条路、焦点归还、无障碍接线，以及「面板真的挂到了 body 上」。
 *
 * 这些原来分散在两个选择器各自的 document 监听里（各写一份、注释几乎逐字相同），
 * 现在只有这一份实现，所以也只在这里钉一次。
 *
 * 定位的算术在 shared/model/tests/popover-position.spec.ts 里单独测——jsdom 的
 * getBoundingClientRect 全返回 0，量不出真实落点，硬测只会测到一堆 0。
 */

const wrappers: VueWrapper[] = []

/** attachTo 是必需的：Esc / 点外面 / 焦点归还都要求元素真的在文档里。 */
function mountPopover(props: Record<string, unknown> = {}) {
  const wrapper = mount(BasePopover, {
    props: { open: false, label: '检索知识库范围', ...props },
    slots: {
      trigger: '<span class="probe-label">所有知识库</span>',
      default: '<p class="probe-body">面板内容</p>',
    },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

function panel(): HTMLElement | null {
  return document.body.querySelector('.popover-panel')
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
}

/** jsdom 不实现 PointerEvent 构造器；监听方只用到 target，普通 Event 足够。 */
function pressOutside(): void {
  document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
}

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  document.body.replaceChildren()
})

describe('BasePopover', () => {
  it('收起时不渲染面板，触发键报 aria-expanded=false', () => {
    const wrapper = mountPopover()

    expect(panel()).toBeNull()
    expect(wrapper.get('button').attributes('aria-expanded')).toBe('false')
  })

  it('点击触发键请求打开，并回传 open 事件', async () => {
    const wrapper = mountPopover()

    await wrapper.get('button').trigger('click')

    expect(wrapper.emitted('update:open')).toEqual([[true]])
    expect(wrapper.emitted('open')).toHaveLength(1)
  })

  it('展开时面板挂到 body 上，带名字与 aria-controls 指回的 id', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    const el = panel()
    expect(el).not.toBeNull()
    expect(el!.getAttribute('role')).toBe('dialog')
    expect(el!.getAttribute('aria-label')).toBe('检索知识库范围')
    expect(wrapper.get('button').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('button').attributes('aria-controls')).toBe(el!.id)
    expect(el!.querySelector('.probe-body')).not.toBeNull()
  })

  /* positioned 是「落点已经量过」的标志，共享层靠它把首帧藏起来——
     首帧的 top / left 还是 (0,0)，不藏会在左上角闪一下。 */
  it('展开后标记为已定位并写下落点', async () => {
    mountPopover({ open: true })
    await nextTick()

    const el = panel()!
    expect(el.classList.contains('is-positioned')).toBe(true)
    expect(el.style.top).not.toBe('')
    expect(el.style.left).not.toBe('')
  })

  it('按 Esc 请求关闭，并把焦点还给触发键', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    pressEscape()

    expect(wrapper.emitted('update:open')).toEqual([[false]])
    expect(document.activeElement).toBe(wrapper.get('button').element)
  })

  it('点面板外面请求关闭', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    pressOutside()

    expect(wrapper.emitted('update:open')).toEqual([[false]])
  })

  /* 多选面板：选一个就关掉的话，连选三个库要重开三次面板。 */
  it('点面板里面不关闭', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    panel()!.dispatchEvent(new Event('pointerdown', { bubbles: true }))

    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('点触发键本身不由「点外面」这条关闭，交给 click 自己翻转', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    wrapper.get('button').element.dispatchEvent(new Event('pointerdown', { bubbles: true }))

    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('收起后不再响应 Esc，也不再有面板', async () => {
    const wrapper = mountPopover({ open: true })
    await nextTick()

    await wrapper.setProps({ open: false })
    pressEscape()

    expect(panel()).toBeNull()
    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('tone=warning 时触发键带提醒语气类', () => {
    const wrapper = mountPopover({ tone: 'warning' })

    expect(wrapper.get('button').classes()).toContain('tone-warning')
  })
})
