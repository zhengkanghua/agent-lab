import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { UserAdminDto } from '@/api/user-admin'
import UserAccountRow from '../components/UserAccountRow.vue'

const CURRENT_USER_ID = '30000000-0000-4000-8000-000000000001'
const OTHER_USER_ID = '30000000-0000-4000-8000-000000000002'

function user(overrides: Partial<UserAdminDto> = {}): UserAdminDto {
  return {
    id: OTHER_USER_ID,
    email: 'member@example.com',
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_environment_admin: false,
    created_at: '2026-08-14T08:00:00Z',
    updated_at: '2026-08-14T08:00:00Z',
    ...overrides,
  }
}

function mountRow(props: Partial<InstanceType<typeof UserAccountRow>['$props']> = {}) {
  return mount(UserAccountRow, {
    props: {
      user: user(),
      busy: false,
      error: '',
      currentUserId: CURRENT_USER_ID,
      resetPassword: null,
      resetError: '',
      ...props,
    },
  })
}

describe('UserAccountRow', () => {
  it('保底管理员的两个开关和重置密码都禁用，但撤销会话仍可点', () => {
    // 这处不对称是有意的：保底管理员的启用状态和权限由部署 Secret 托管、网页改不动，
    // 但它的会话该能撤销——密码泄露时管理员需要立刻把已登录的会话踢掉，而那不需要
    // 改任何账号字段。把撤销会话一起禁掉会让唯一的应急手段消失。
    const managed = user({ id: CURRENT_USER_ID, is_environment_admin: true, is_superuser: true })
    const wrapper = mountRow({ user: managed })

    expect(wrapper.get(`[data-testid="active-${managed.id}"]`).attributes('disabled')).toBeDefined()
    expect(
      wrapper.get(`[data-testid="superuser-${managed.id}"]`).attributes('disabled'),
    ).toBeDefined()
    expect(wrapper.get(`[data-testid="reset-${managed.id}"]`).attributes('disabled')).toBeDefined()
    expect(
      wrapper.get(`[data-testid="sessions-${managed.id}"]`).attributes('disabled'),
    ).toBeUndefined()
  })

  it('禁用的控件都带 title 说明原因，不让人以为是坏了', () => {
    const managed = user({ is_environment_admin: true })
    const wrapper = mountRow({ user: managed })

    const titles = wrapper.findAll('[title]').map((node) => node.attributes('title'))
    expect(titles).toContain('由部署 Secret 管理')
    expect(titles).toContain('保底管理员必须保持超级用户')
    expect(titles).toContain('请修改部署 Secret 后重启服务')
  })

  it('请求在途时整行控件全部禁用，含撤销会话', () => {
    // 同一行并发两次改动会让「最后写入者胜出」，用户看到的结果取决于哪个请求先回来。
    const wrapper = mountRow({ busy: true })
    const id = user().id

    for (const testid of [`active-${id}`, `superuser-${id}`, `reset-${id}`, `sessions-${id}`]) {
      expect(wrapper.get(`[data-testid="${testid}"]`).attributes('disabled')).toBeDefined()
    }
  })

  it('开关发出的是勾选框的新状态，不是取反后的旧值', async () => {
    // 这两个转发函数从 event.target.checked 取值。写成 !user.is_active 也能在单击时
    // 碰巧正确，但父组件失败回滚后 DOM 与 prop 会短暂不一致，那时取反给出的是错的。
    const wrapper = mountRow({ user: user({ is_active: true, is_superuser: false }) })

    await wrapper.get(`[data-testid="active-${OTHER_USER_ID}"]`).setValue(false)
    await wrapper.get(`[data-testid="superuser-${OTHER_USER_ID}"]`).setValue(true)

    expect(wrapper.emitted('set-active')).toEqual([[false]])
    expect(wrapper.emitted('set-superuser')).toEqual([[true]])
  })

  it('三种身份各给一句说明，当前账号能被认出来', () => {
    expect(mountRow({ user: user({ is_environment_admin: true }) }).text()).toContain(
      '由部署 Secret 托管',
    )
    expect(mountRow({ user: user({ id: CURRENT_USER_ID }) }).text()).toContain('当前账号')
    expect(mountRow().text()).toContain('数据库账号')
  })

  it('resetPassword 为 null 时不渲染重置表单，给了字符串才展开', () => {
    // 用 null 而不是空串表示「没展开」：空串是合法的输入中间态（用户清空了输入框），
    // 两者混用会让清空输入等于关掉表单。
    expect(mountRow({ resetPassword: null }).find('.row-reset').exists()).toBe(false)
    expect(mountRow({ resetPassword: '' }).find('.row-reset').exists()).toBe(true)
  })

  it('行内失败原因用 alert 播报，读屏用户不会漏掉', async () => {
    const wrapper = mountRow({ error: '该账号是最后一个超级用户。' })

    const alert = wrapper.get('[role="alert"]')
    expect(alert.text()).toBe('该账号是最后一个超级用户。')
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)

    await wrapper.setProps({ error: '' })
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('两个动作按钮各自发出对应事件', async () => {
    const wrapper = mountRow()

    await wrapper.get(`[data-testid="reset-${OTHER_USER_ID}"]`).trigger('click')
    await wrapper.get(`[data-testid="sessions-${OTHER_USER_ID}"]`).trigger('click')

    expect(wrapper.emitted('open-reset')).toHaveLength(1)
    expect(wrapper.emitted('revoke-sessions')).toHaveLength(1)
  })
})
