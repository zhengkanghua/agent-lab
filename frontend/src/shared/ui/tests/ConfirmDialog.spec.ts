import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import ConfirmDialog from '../ConfirmDialog.vue'
import {
  pendingConfirm,
  requestConfirm,
  resetConfirm,
  settleConfirm,
} from '@/shared/composables/confirm'

/* 确认框自己的行为。
 *
 * 挂到 document 上（attachTo）：焦点断言依赖元素真的在文档里——jsdom 对游离元素调 focus()
 * 不会改 document.activeElement，不挂就测不出「初始焦点在取消上」这条关键设计。
 */

let wrapper: VueWrapper | undefined

function mountDialog(): VueWrapper {
  wrapper = mount(ConfirmDialog, { attachTo: document.body })
  return wrapper
}

function actionButtons(current: VueWrapper) {
  return current.findAll('.confirm-actions button')
}

beforeEach(() => {
  resetConfirm()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.replaceChildren()
  // 模块级单例跨用例存活：留着会让下一条用例一上来就是个打开态的确认框。
  resetConfirm()
})

describe('ConfirmDialog', () => {
  it('没有请求时什么都不渲染', () => {
    const current = mountDialog()

    expect(current.find('.base-dialog').exists()).toBe(false)
    expect(pendingConfirm.value).toBeNull()
  })

  it('请求到达后渲染标题、说明与两个动作键，角色是 alertdialog', async () => {
    const current = mountDialog()
    void requestConfirm({
      title: '删除这个会话？',
      description: '「央行利率」的对话历史会一起清除，且无法恢复。',
      confirmLabel: '删除会话',
      tone: 'danger',
    })
    await flushPromises()

    expect(current.get('.confirm-title').text()).toBe('删除这个会话？')
    expect(current.get('.confirm-description').text()).toContain('无法恢复')
    expect(actionButtons(current).map((button) => button.text())).toEqual(['取消', '删除会话'])
    expect(current.get('.base-dialog').attributes('role')).toBe('alertdialog')
  })

  it('确认键用 danger 变体，取消键是描边——两者在颜色上先分开', async () => {
    const current = mountDialog()
    void requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    const [cancel, confirm] = actionButtons(current)
    expect(cancel!.classes()).toContain('is-outline')
    expect(confirm!.classes()).toContain('is-danger')
  })

  it('初始焦点落在「取消」上：一路回车不该执行不可恢复的动作', async () => {
    const current = mountDialog()
    void requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    expect(document.activeElement).toBe(actionButtons(current)[0]!.element)
  })

  it('点确认结为 true 并关闭', async () => {
    const current = mountDialog()
    const answer = requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    await actionButtons(current)[1]!.trigger('click')

    await expect(answer).resolves.toBe(true)
    expect(pendingConfirm.value).toBeNull()
    expect(current.find('.base-dialog').exists()).toBe(false)
  })

  it('点取消结为 false', async () => {
    const current = mountDialog()
    const answer = requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    await actionButtons(current)[0]!.trigger('click')

    await expect(answer).resolves.toBe(false)
    expect(pendingConfirm.value).toBeNull()
  })

  it('Esc 等于取消，而不是确认', async () => {
    mountDialog()
    const answer = requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()

    await expect(answer).resolves.toBe(false)
    expect(pendingConfirm.value).toBeNull()
  })

  it('点遮罩不关闭：取消是安全方向，也不该被误触丢掉一次想清楚的确认', async () => {
    const current = mountDialog()
    const answer = requestConfirm({ title: '删除？', confirmLabel: '删除', tone: 'danger' })
    await flushPromises()

    await current.get('.base-dialog-overlay').trigger('click')

    expect(current.find('.base-dialog').exists()).toBe(true)
    expect(pendingConfirm.value).not.toBeNull()
    settleConfirm(false)
    await expect(answer).resolves.toBe(false)
  })

  it('同一时刻只留一个请求：新请求把上一个按取消结掉，不是排队', async () => {
    const current = mountDialog()
    const first = requestConfirm({ title: '第一个', confirmLabel: '删' })
    await flushPromises()

    const second = requestConfirm({ title: '第二个', confirmLabel: '删' })
    await flushPromises()

    await expect(first).resolves.toBe(false)
    expect(current.get('.confirm-title').text()).toBe('第二个')

    settleConfirm(true)
    await expect(second).resolves.toBe(true)
  })
})
