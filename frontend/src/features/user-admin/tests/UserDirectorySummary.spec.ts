import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UserDirectorySummary from '../components/UserDirectorySummary.vue'

describe('UserDirectorySummary', () => {
  it('三个数字按「全部/启用/超级用户」的顺序给出', () => {
    // 顺序也是契约：三个数字长得一样，错序时页面看起来完全正常，只是数字对错了标签。
    const wrapper = mount(UserDirectorySummary, {
      props: { stats: { total: 7, active: 5, superusers: 2 } },
    })

    const cells = wrapper.findAll('.account-summary span')
    expect(cells[0]?.text()).toContain('7')
    expect(cells[0]?.text()).toContain('全部账号')
    expect(cells[1]?.text()).toContain('5')
    expect(cells[1]?.text()).toContain('启用')
    expect(cells[2]?.text()).toContain('2')
    expect(cells[2]?.text()).toContain('超级用户')
  })

  it('零值照常显示，不被当成「没数据」隐藏掉', () => {
    const wrapper = mount(UserDirectorySummary, {
      props: { stats: { total: 0, active: 0, superusers: 0 } },
    })

    expect(wrapper.findAll('.account-summary strong').map((node) => node.text())).toEqual([
      '0',
      '0',
      '0',
    ])
  })

  it('带一句常驻说明，并给整块一个无障碍名字', () => {
    const wrapper = mount(UserDirectorySummary, {
      props: { stats: { total: 1, active: 1, superusers: 1 } },
    })

    expect(wrapper.get('.summary-note').text()).toBe('密码与会话仅保存在服务端')
    expect(wrapper.get('section').attributes('aria-label')).toBe('账号概况')
  })
})
