import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { UserAdminDto } from '@/api/user-admin'
import UserDirectoryTable from '../components/UserDirectoryTable.vue'

const FIRST_ID = '30000000-0000-4000-8000-000000000001'
const SECOND_ID = '30000000-0000-4000-8000-000000000002'

function user(id: string, overrides: Partial<UserAdminDto> = {}): UserAdminDto {
  return {
    id,
    email: `${id.slice(-1)}@example.com`,
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_environment_admin: false,
    deleted_at: null,
    created_at: '2026-08-14T08:00:00Z',
    updated_at: '2026-08-14T08:00:00Z',
    ...overrides,
  }
}

function mountTable(props: Partial<InstanceType<typeof UserDirectoryTable>['$props']> = {}) {
  return mount(UserDirectoryTable, {
    props: {
      users: [user(FIRST_ID), user(SECOND_ID)],
      loadState: 'ready' as const,
      loadError: '',
      busyUserIds: new Set<string>(),
      rowErrors: {},
      currentUserId: FIRST_ID,
      includeDeleted: false,
      resetUserId: null,
      resetPassword: '',
      resetError: '',
      ...props,
    },
  })
}

describe('UserDirectoryTable', () => {
  it('加载中显示 status，不显示表格', () => {
    const wrapper = mountTable({ loadState: 'loading' })

    expect(wrapper.get('[role="status"]').text()).toContain('正在读取账号目录')
    expect(wrapper.find('[role="table"]').exists()).toBe(false)
    // 加载中禁用刷新键，避免连点叠出多个请求。
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  })

  it('加载失败显示 alert 并给出重新加载', async () => {
    const wrapper = mountTable({ loadState: 'error', loadError: '账号服务暂时不可用。' })

    expect(wrapper.get('[role="alert"]').text()).toContain('账号服务暂时不可用。')
    expect(wrapper.find('[role="table"]').exists()).toBe(false)

    await wrapper.get('[role="alert"] button').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('就绪但没有账号时给一句说明，而不是一张空表', () => {
    const wrapper = mountTable({ users: [] })

    expect(wrapper.text()).toContain('当前还没有可管理账号。')
    expect(wrapper.find('[role="table"]').exists()).toBe(false)
  })

  it('密码重置表单只在目标账号所在行展开，清空输入不会关闭', async () => {
    const wrapper = mountTable({ resetUserId: SECOND_ID, resetPassword: 'draft' })

    expect(
      wrapper.get(`[data-user-id="${FIRST_ID}"]`).find('input[name="reset-password"]').exists(),
    ).toBe(false)
    expect(
      wrapper.get<HTMLInputElement>(`[data-user-id="${SECOND_ID}"] input[name="reset-password"]`)
        .element.value,
    ).toBe('draft')
    await wrapper.setProps({ resetPassword: '' })
    expect(wrapper.get<HTMLInputElement>('input[name="reset-password"]').element.value).toBe('')
    await wrapper.setProps({ resetUserId: null })
    expect(wrapper.find('input[name="reset-password"]').exists()).toBe(false)
  })

  it('在途状态和行内错误各自落到对应的行上', async () => {
    const wrapper = mountTable({
      busyUserIds: new Set([SECOND_ID]),
      rowErrors: { [FIRST_ID]: '该账号是最后一个超级用户。' },
    })
    const first = wrapper.get(`[data-user-id="${FIRST_ID}"]`)
    const second = wrapper.get(`[data-user-id="${SECOND_ID}"]`)

    // 空闲那一行里，除「注销账号」外都不该被禁用。注销键另有自己的禁用条件（当前账号、
    // 环境托管超级用户），不随在途状态走，所以从这条断言里排掉它——它由下面那条用例单独覆盖。
    const controls = first
      .findAll('input, button')
      .filter((control) => !control.attributes('data-testid')?.startsWith('delete-'))
    expect(controls.every((control) => control.attributes('disabled') === undefined)).toBe(true)
    expect(first.get('[role="alert"]').text()).toBe('该账号是最后一个超级用户。')
    for (const control of second.findAll('input, button')) {
      expect(control.attributes('disabled')).toBeDefined()
    }
    expect(second.find('[role="alert"]').exists()).toBe(false)
    await wrapper.setProps({ rowErrors: {} })
    expect(first.find('[role="alert"]').exists()).toBe(false)
  })

  it('启用状态开关等待确认，失败后仍可重试同一目标状态', async () => {
    // 「开关显示的是服务端已确认的状态」：点击先改变原生控件，请求在途或失败时
    // 显示值仍是接口确认的那个，否则界面会先说自己成功、再被打回来。
    const field = 'active'
    const event = 'set-active'
    const confirmed = true
    const wrapper = mountTable()
    const input = wrapper.get<HTMLInputElement>(`[data-testid="${field}-${SECOND_ID}"]`)
    await input.setValue(!confirmed)
    expect(input.element.checked).toBe(confirmed)
    await wrapper.setProps({ busyUserIds: new Set([SECOND_ID]) })
    await wrapper.setProps({
      busyUserIds: new Set(),
      rowErrors: { [SECOND_ID]: '请求失败，请重试。' },
    })
    await input.setValue(!confirmed)
    expect(input.element.checked).toBe(confirmed)
    expect(wrapper.emitted(event)).toEqual([
      [expect.objectContaining({ id: SECOND_ID }), !confirmed],
      [expect.objectContaining({ id: SECOND_ID }), !confirmed],
    ])
    wrapper.unmount()
  })

  it('行事件往上转时带的是那一行的账号对象', async () => {
    // 转错对象的后果最严重：点第二行的开关，改的是第一行的账号。
    const wrapper = mountTable()
    await wrapper.get(`[data-testid="active-${SECOND_ID}"]`).setValue(false)
    await wrapper.get(`[data-testid="reset-${SECOND_ID}"]`).trigger('click')
    await wrapper.get(`[data-testid="sessions-${SECOND_ID}"]`).trigger('click')
    await wrapper.get(`[data-testid="delete-${SECOND_ID}"]`).trigger('click')

    expect(wrapper.emitted('set-active')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('set-active')?.[0]?.[1]).toBe(false)
    expect(wrapper.emitted('open-reset')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('revoke-sessions')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('delete-account')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
  })

  it('当前账号那一行不提供停用/启用与注销键，其余行提供', () => {
    // currentUserId 是 FIRST_ID：那一行是操作者自己，两个动作都会让他当场失去权限。
    // 界面不提供只是体验，真正的边界是后端那条 account_self_protected。
    const wrapper = mountTable()

    expect(wrapper.find(`[data-testid="active-${FIRST_ID}"]`).exists()).toBe(false)
    expect(wrapper.find(`[data-testid="delete-${FIRST_ID}"]`).exists()).toBe(false)
    // 状态本身还是要看得见：只撤控件，不撤信息。
    expect(wrapper.text()).toContain('启用')
    // 撤销会话与重置密码不在这条规则里：它们不会让人失去权限。
    expect(wrapper.find(`[data-testid="reset-${FIRST_ID}"]`).exists()).toBe(true)
    expect(wrapper.find(`[data-testid="sessions-${FIRST_ID}"]`).exists()).toBe(true)
    // 别人那一行照常。
    expect(wrapper.find(`[data-testid="active-${SECOND_ID}"]`).exists()).toBe(true)
    expect(wrapper.find(`[data-testid="delete-${SECOND_ID}"]`).exists()).toBe(true)
  })

  it('环境托管账号的启用键与注销键禁用，且界面里不再有改超管身份的入口', () => {
    const managed = mountTable({
      users: [user(FIRST_ID, { is_environment_admin: true, is_superuser: true })],
      currentUserId: undefined,
    })

    expect(managed.get(`[data-testid="active-${FIRST_ID}"]`).attributes('disabled')).toBeDefined()
    expect(managed.get(`[data-testid="delete-${FIRST_ID}"]`).attributes('disabled')).toBeDefined()
    // 超管身份只在建号时决定，行里没有能改它的控件——任何一行都没有。
    expect(managed.find(`[data-testid="superuser-${FIRST_ID}"]`).exists()).toBe(false)
    expect(managed.text()).toContain('超级用户')
  })

  it('刷新键发出 refresh', async () => {
    const wrapper = mountTable()

    await wrapper.get('button').trigger('click')

    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('「显示已注销」开关把新值往上转，默认是关的', async () => {
    const wrapper = mountTable()
    const toggle = wrapper.get<HTMLInputElement>('[data-testid="include-deleted"]')

    expect(toggle.element.checked).toBe(false)
    await toggle.setValue(true)

    expect(wrapper.emitted('update:includeDeleted')).toEqual([[true]])
  })

  it('已注销的行显示标记与注销时间，且不提供停用/启用、注销、重置密码', () => {
    const wrapper = mountTable({
      users: [
        user(FIRST_ID),
        user(SECOND_ID, { is_active: false, deleted_at: '2026-09-01T00:00:00Z' }),
      ],
    })
    const row = wrapper.get(`[data-user-id="${SECOND_ID}"]`)

    expect(row.text()).toContain('已注销')
    expect(row.text()).toContain('2026')
    // 注销是终态：没有改回去的开关，也不再有可改的密码或可再走一次的注销。
    expect(row.find(`[data-testid="active-${SECOND_ID}"]`).exists()).toBe(false)
    expect(row.find(`[data-testid="reset-${SECOND_ID}"]`).exists()).toBe(false)
    expect(row.find(`[data-testid="delete-${SECOND_ID}"]`).exists()).toBe(false)
    // 撤销会话是实际动作，保留。
    expect(row.find(`[data-testid="sessions-${SECOND_ID}"]`).exists()).toBe(true)
  })
})
