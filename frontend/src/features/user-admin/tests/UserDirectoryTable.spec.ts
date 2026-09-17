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

    // 空闲那一行里，除「删除账号」外都不该被禁用。删除键另有自己的禁用条件（当前账号、
    // 保底管理员），不随在途状态走，所以从这条断言里排掉它——它由下面那条用例单独覆盖。
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

  it.each([
    { field: 'active', event: 'set-active', confirmed: true },
    { field: 'superuser', event: 'set-superuser', confirmed: false },
  ])('$field 开关等待确认，失败后仍可重试同一目标状态', async ({ field, event, confirmed }) => {
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
    await wrapper.get(`[data-testid="superuser-${SECOND_ID}"]`).setValue(true)
    await wrapper.get(`[data-testid="reset-${SECOND_ID}"]`).trigger('click')
    await wrapper.get(`[data-testid="sessions-${SECOND_ID}"]`).trigger('click')
    await wrapper.get(`[data-testid="delete-${SECOND_ID}"]`).trigger('click')

    expect(wrapper.emitted('set-active')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('set-active')?.[0]?.[1]).toBe(false)
    expect(wrapper.emitted('set-superuser')?.[0]).toEqual([
      expect.objectContaining({ id: SECOND_ID }),
      true,
    ])
    expect(wrapper.emitted('open-reset')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('revoke-sessions')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('delete-account')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
  })

  it('保底管理员与当前账号的删除键禁用，其余行可用', () => {
    // currentUserId 是 FIRST_ID，所以第一行（当前账号）禁用、第二行可用。
    const wrapper = mountTable()
    expect(wrapper.get(`[data-testid="delete-${FIRST_ID}"]`).attributes('disabled')).toBeDefined()
    expect(
      wrapper.get(`[data-testid="delete-${SECOND_ID}"]`).attributes('disabled'),
    ).toBeUndefined()

    const managed = mountTable({
      users: [user(FIRST_ID, { is_environment_admin: true })],
      currentUserId: undefined,
    })
    expect(managed.get(`[data-testid="delete-${FIRST_ID}"]`).attributes('disabled')).toBeDefined()
  })

  it('刷新键发出 refresh', async () => {
    const wrapper = mountTable()

    await wrapper.get('button').trigger('click')

    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })
})
