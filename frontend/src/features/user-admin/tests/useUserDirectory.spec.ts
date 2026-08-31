import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  revokeUserSessions: vi.fn(),
  createUser: vi.fn(),
}))

vi.mock('../../../api/user-admin', () => api)

import { ApiError } from '@/api/client'
import type { UserAdminDto } from '@/api/user-admin'
import { useUserDirectory } from '../composables/useUserDirectory'

const environmentAdmin: UserAdminDto = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'admin@example.com',
  is_active: true,
  is_superuser: true,
  is_verified: true,
  is_environment_admin: true,
  created_at: '2026-08-17T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

const reader: UserAdminDto = {
  id: '20000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

/** 与 useAgentDefaultPrompt 的用例同理：onScopeDispose 的取消语义需要真实的 effect scope。 */
function mountHarness(currentUserId: string | undefined = environmentAdmin.id) {
  const onSelfDowngraded = vi.fn(async () => undefined)
  let composable: ReturnType<typeof useUserDirectory> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useUserDirectory({ currentUserId: () => currentUserId, onSelfDowngraded })
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, directory: composable, onSelfDowngraded }
}

describe('useUserDirectory', () => {
  beforeEach(() => {
    api.listUsers.mockReset()
    api.updateUser.mockReset()
    api.resetUserPassword.mockReset()
    api.revokeUserSessions.mockReset()
    api.listUsers.mockResolvedValue([reader, environmentAdmin])
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('读到列表后排好序并给出概况', async () => {
    const { wrapper, directory } = mountHarness()

    await directory.load()

    expect(directory.loadState.value).toBe('ready')
    expect(directory.users.value.map((user) => user.email)).toEqual([
      'admin@example.com',
      'reader@example.com',
    ])
    expect(directory.stats.value).toEqual({ total: 2, active: 2, superusers: 1 })
    wrapper.unmount()
  })

  it('读取失败时留下文案并停在 error 态', async () => {
    api.listUsers.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'postgresql_unavailable', status: 503 }),
    )
    const { wrapper, directory } = mountHarness()

    await directory.load()

    expect(directory.loadState.value).toBe('error')
    expect(directory.loadError.value).not.toBe('')
    expect(directory.users.value).toEqual([])
    wrapper.unmount()
  })

  it('连点刷新时丢弃先发的那一条响应', async () => {
    let settleFirst: ((value: UserAdminDto[]) => void) | undefined
    const stale: UserAdminDto = { ...reader, email: 'stale@example.com' }
    api.listUsers
      .mockImplementationOnce(() => new Promise((resolve) => (settleFirst = resolve)))
      .mockResolvedValueOnce([reader])
    const { wrapper, directory } = mountHarness()

    const first = directory.load()
    await nextTick()
    await directory.load()
    // 后发的先回、先发的后回：只比 signal.aborted 拦不住这一条，所以另外比 controller 身份。
    settleFirst?.([stale])
    await first
    await flushPromises()

    expect(directory.users.value.map((user) => user.email)).toEqual(['reader@example.com'])
    wrapper.unmount()
  })

  it('改状态成功后替换那一行并留下提示', async () => {
    api.updateUser.mockResolvedValue({ ...reader, is_active: false })
    const { wrapper, directory } = mountHarness()
    await directory.load()

    await directory.setActive(reader, false)

    expect(api.updateUser).toHaveBeenCalledWith({ userId: reader.id, isActive: false })
    expect(directory.users.value.find((user) => user.id === reader.id)?.is_active).toBe(false)
    expect(directory.feedback.value).toContain(reader.email)
    expect(directory.rowErrors.value[reader.id]).toBe('')
    wrapper.unmount()
  })

  it('保底管理员不发请求', async () => {
    const { wrapper, directory } = mountHarness()
    await directory.load()

    await directory.setActive(environmentAdmin, false)
    await directory.setSuperuser(environmentAdmin, false)
    directory.openPasswordReset(environmentAdmin)

    expect(api.updateUser).not.toHaveBeenCalled()
    expect(directory.resetUserId.value).toBeNull()
    wrapper.unmount()
  })

  it('行内操作失败后解除忙态，错误落在那一行', async () => {
    api.updateUser.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'user_not_found', status: 404 }),
    )
    const { wrapper, directory } = mountHarness()
    await directory.load()

    await directory.setActive(reader, false)

    // 漏掉 finally 会把这一行永久留在禁用态，页面上只表现为「按钮再也点不动」。
    expect(directory.isBusy(reader.id)).toBe(false)
    expect(directory.rowErrors.value[reader.id]).not.toBe('')
    expect(directory.feedback.value).toBe('')
    wrapper.unmount()
  })

  it('操作进行中不接受同一行的第二次点击', async () => {
    let settle: ((value: UserAdminDto) => void) | undefined
    api.updateUser.mockImplementation(() => new Promise((resolve) => (settle = resolve)))
    const { wrapper, directory } = mountHarness()
    await directory.load()

    const pending = directory.setActive(reader, false)
    await nextTick()
    expect(directory.isBusy(reader.id)).toBe(true)
    await directory.setActive(reader, true)

    expect(api.updateUser).toHaveBeenCalledTimes(1)
    settle?.({ ...reader, is_active: false })
    await pending
    wrapper.unmount()
  })

  it('当前账号把自己降级后交给页面处理', async () => {
    api.updateUser.mockResolvedValue({ ...reader, is_superuser: false })
    const { wrapper, directory, onSelfDowngraded } = mountHarness(reader.id)
    await directory.load()

    await directory.setSuperuser(reader, false)

    expect(onSelfDowngraded).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('降级别人不触发自降级处理', async () => {
    api.updateUser.mockResolvedValue({ ...reader, is_superuser: false })
    const { wrapper, directory, onSelfDowngraded } = mountHarness(environmentAdmin.id)
    await directory.load()

    await directory.setSuperuser(reader, false)

    expect(onSelfDowngraded).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('把自己从停用改回启用不算降级', async () => {
    api.updateUser.mockResolvedValue({ ...reader, is_active: true, is_superuser: true })
    const { wrapper, directory, onSelfDowngraded } = mountHarness(reader.id)
    await directory.load()

    await directory.setActive(reader, true)

    expect(onSelfDowngraded).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('密码不合规时不发请求，错误落在重置表单', async () => {
    const { wrapper, directory } = mountHarness()
    await directory.load()
    directory.openPasswordReset(reader)
    directory.resetPassword.value = 'short'

    await directory.submitPasswordReset(reader)

    expect(api.resetUserPassword).not.toHaveBeenCalled()
    expect(directory.resetError.value).not.toBe('')
    // 校验的结论贴在表单旁边，不写到那一行的错误位：两处都显示会让人以为出了两个问题。
    expect(directory.rowErrors.value[reader.id]).toBe('')
    wrapper.unmount()
  })

  it('重置成功后收起表单、清掉密码', async () => {
    api.resetUserPassword.mockResolvedValue(reader)
    const { wrapper, directory } = mountHarness()
    await directory.load()
    directory.openPasswordReset(reader)
    directory.resetPassword.value = 'a'.repeat(12)

    await directory.submitPasswordReset(reader)

    expect(directory.resetUserId.value).toBeNull()
    expect(directory.resetPassword.value).toBe('')
    expect(directory.feedback.value).toContain('撤销该账号的全部会话')
    wrapper.unmount()
  })

  it('重置失败时错误留在表单里，密码不清掉', async () => {
    api.resetUserPassword.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'invalid_password', status: 400 }),
    )
    const { wrapper, directory } = mountHarness()
    await directory.load()
    directory.openPasswordReset(reader)
    directory.resetPassword.value = 'a'.repeat(12)

    await directory.submitPasswordReset(reader)

    // 表单还开着、错误贴在它旁边，管理员可以就地改一个密码重试。
    expect(directory.resetUserId.value).toBe(reader.id)
    expect(directory.resetError.value).not.toBe('')
    expect(directory.rowErrors.value[reader.id]).toBe('')
    wrapper.unmount()
  })

  it('再点同一行是收起重置表单', async () => {
    const { wrapper, directory } = mountHarness()
    await directory.load()

    directory.openPasswordReset(reader)
    expect(directory.resetUserId.value).toBe(reader.id)
    directory.openPasswordReset(reader)
    expect(directory.resetUserId.value).toBeNull()
    wrapper.unmount()
  })

  it('撤销会话要先确认，拒绝就什么都不做', async () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    const { wrapper, directory } = mountHarness()
    await directory.load()

    await directory.revokeSessions(reader)

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(api.revokeUserSessions).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('没有有效会话时的提示与撤销掉若干个不同', async () => {
    vi.stubGlobal(
      'confirm',
      vi.fn(() => true),
    )
    api.revokeUserSessions.mockResolvedValueOnce({ revoked_sessions: 0 })
    const { wrapper, directory } = mountHarness()
    await directory.load()

    await directory.revokeSessions(reader)
    expect(directory.feedback.value).toContain('当前没有有效会话')

    api.revokeUserSessions.mockResolvedValueOnce({ revoked_sessions: 3 })
    await directory.revokeSessions(reader)
    expect(directory.feedback.value).toContain('3')
    wrapper.unmount()
  })

  it('并进来的新账号参与排序', async () => {
    const { wrapper, directory } = mountHarness()
    await directory.load()

    directory.acceptCreatedUser({ ...reader, id: 'a', email: 'alice@example.com' })

    expect(directory.users.value.map((user) => user.email)).toEqual([
      'admin@example.com',
      'alice@example.com',
      'reader@example.com',
    ])
    expect(directory.feedback.value).toContain('alice@example.com')
    wrapper.unmount()
  })

  it('卸载时中止在飞的读取并清掉密码', async () => {
    api.listUsers.mockImplementation(() => new Promise(() => undefined))
    const { wrapper, directory } = mountHarness()
    void directory.load()
    await nextTick()
    directory.resetPassword.value = 'a'.repeat(12)

    wrapper.unmount()

    expect(api.listUsers.mock.calls[0]?.[0]?.aborted).toBe(true)
    expect(directory.resetPassword.value).toBe('')
  })
})
