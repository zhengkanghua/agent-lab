import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UserDirectorySummary from '../components/UserDirectorySummary.vue'

/* 概况的三个数字由目录列表算出来。列表没读回来时它是三个 0，而这块是无条件渲染的——
 * 加载中用户会读到「0 全部账号 / 0 启用 / 0 超级用户」，读回后才跳成真实数字。
 * 先闪一个错值比转圈更糟：转圈是「等一下」，闪 0 是「这里没账号」。
 */

const stats = { total: 3, active: 2, superusers: 1 }

function numbers(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.account-summary strong').map((el) => el.text())
}

describe('UserDirectorySummary', () => {
  it('目录读回来之后显示真实数字', () => {
    const wrapper = mount(UserDirectorySummary, { props: { stats } })

    expect(numbers(wrapper)).toEqual(['3', '2', '1'])
  })

  it('还没读回来时数字位置是破折号，不是 0', () => {
    const wrapper = mount(UserDirectorySummary, {
      props: { stats: { total: 0, active: 0, superusers: 0 }, pending: true },
    })

    expect(numbers(wrapper)).toEqual(['—', '—', '—'])
  })

  it('标签保持不动，读回来时不会跳动', () => {
    const wrapper = mount(UserDirectorySummary, {
      props: { stats: { total: 0, active: 0, superusers: 0 }, pending: true },
    })

    expect(wrapper.text()).toContain('全部账号')
    expect(wrapper.text()).toContain('启用')
    expect(wrapper.text()).toContain('超级用户')
  })
})
