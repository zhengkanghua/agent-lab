import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { UserAdminDto } from '@/api/user-admin'
import UserDirectoryTable from '../components/UserDirectoryTable.vue'
import UserAccountRow from '../components/UserAccountRow.vue'

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
    expect(wrapper.get('.directory-heading .base-button').attributes('disabled')).toBeDefined()
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

  it('就绪后每个账号一行，表头列数与行对齐', () => {
    const wrapper = mountTable()

    expect(wrapper.findAllComponents(UserAccountRow)).toHaveLength(2)
    expect(wrapper.findAll('[role="columnheader"]')).toHaveLength(5)
  })

  it('密码重置表单只发给展开的那一行，其余行拿到 null', () => {
    // 这是本组件唯一的一处逻辑（resetPasswordFor）。传错的后果是两行同时展开表单，
    // 或者改密码时改到了别人。
    const wrapper = mountTable({ resetUserId: SECOND_ID, resetPassword: 'draft' })
    const rows = wrapper.findAllComponents(UserAccountRow)

    expect(rows[0]?.props('resetPassword')).toBeNull()
    expect(rows[1]?.props('resetPassword')).toBe('draft')
  })

  it('在途状态和行内错误各自落到对应的行上', () => {
    const wrapper = mountTable({
      busyUserIds: new Set([SECOND_ID]),
      rowErrors: { [FIRST_ID]: '该账号是最后一个超级用户。' },
    })
    const rows = wrapper.findAllComponents(UserAccountRow)

    expect(rows[0]?.props('busy')).toBe(false)
    expect(rows[0]?.props('error')).toBe('该账号是最后一个超级用户。')
    expect(rows[1]?.props('busy')).toBe(true)
    expect(rows[1]?.props('error')).toBe('')
  })

  it('行事件往上转时带的是那一行的账号对象', async () => {
    // 转错对象的后果最严重：点第二行的开关，改的是第一行的账号。
    const wrapper = mountTable()
    const second = wrapper.findAllComponents(UserAccountRow)[1]

    second?.vm.$emit('set-active', false)
    second?.vm.$emit('set-superuser', true)
    second?.vm.$emit('open-reset')
    second?.vm.$emit('revoke-sessions')
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('set-active')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('set-active')?.[0]?.[1]).toBe(false)
    expect(wrapper.emitted('set-superuser')?.[0]).toEqual([
      expect.objectContaining({ id: SECOND_ID }),
      true,
    ])
    expect(wrapper.emitted('open-reset')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
    expect(wrapper.emitted('revoke-sessions')?.[0]?.[0]).toMatchObject({ id: SECOND_ID })
  })

  it('刷新键发出 refresh', async () => {
    const wrapper = mountTable()

    await wrapper.get('.directory-heading .base-button').trigger('click')

    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })
})
