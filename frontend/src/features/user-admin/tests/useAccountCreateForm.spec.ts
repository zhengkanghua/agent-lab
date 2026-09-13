// @vitest-environment node
import { effectScope, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ createUser: vi.fn() }))

vi.mock('../../../api/user-admin', () => api)

import { ApiError } from '@/api/client'
import type { UserAdminDto } from '@/api/user-admin'
import { useAccountCreateForm } from '../composables/useAccountCreateForm'

const created: UserAdminDto = {
  id: '20000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

function createForm() {
  const onCreated = vi.fn()
  const scope = effectScope()
  const form = scope.run(() => useAccountCreateForm({ onCreated, onOpen: () => {} }))
  if (!form) throw new Error('Test scope did not initialize composable')
  return { scope, form, onCreated }
}

/** 填一份能过本地校验的输入。 */
function fill(form: ReturnType<typeof useAccountCreateForm>): void {
  form.email.value = 'reader@example.com'
  form.password.value = 'a'.repeat(12)
}

describe('useAccountCreateForm', () => {
  beforeEach(() => {
    api.createUser.mockReset()
  })

  it('本地校验不过就不发请求', async () => {
    const { scope, form, onCreated } = createForm()
    form.open()
    form.email.value = 'not-an-email'
    form.password.value = 'a'.repeat(12)

    await form.submit()

    expect(api.createUser).not.toHaveBeenCalled()
    expect(form.error.value).not.toBe('')
    expect(onCreated).not.toHaveBeenCalled()
    expect(form.expanded.value).toBe(true)
    scope.stop()
  })

  it('规范化邮箱提交，成功后清空输入并交出新账号', async () => {
    api.createUser.mockResolvedValue(created)
    const { scope, form, onCreated } = createForm()
    form.open()
    fill(form)
    form.email.value = '  reader@example.com  '
    form.superuser.value = true

    await form.submit()

    expect(api.createUser).toHaveBeenCalledWith({
      email: 'reader@example.com',
      password: 'a'.repeat(12),
      isSuperuser: true,
    })
    expect(form.expanded.value).toBe(false)
    expect(form.email.value).toBe('')
    expect(form.password.value).toBe('')
    expect(form.superuser.value).toBe(false)
    expect(onCreated).toHaveBeenCalledWith(created)
    scope.stop()
  })

  it('创建失败时表单留着、输入不丢', async () => {
    api.createUser.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'email_already_exists', status: 409 }),
    )
    const { scope, form, onCreated } = createForm()
    form.open()
    fill(form)

    await form.submit()

    expect(form.expanded.value).toBe(true)
    expect(form.email.value).toBe('reader@example.com')
    expect(form.password.value).toBe('a'.repeat(12))
    expect(form.error.value).not.toBe('')
    expect(form.submitting.value).toBe(false)
    expect(onCreated).not.toHaveBeenCalled()
    scope.stop()
  })

  it('提交中不许关、也不许再提交一次', async () => {
    let settle: ((value: UserAdminDto) => void) | undefined
    api.createUser.mockImplementation(() => new Promise((resolve) => (settle = resolve)))
    const { scope, form } = createForm()
    form.open()
    fill(form)

    const pending = form.submit()
    await nextTick()
    expect(form.submitting.value).toBe(true)
    form.close()
    await form.submit()

    // 关掉表单会让人以为已经取消了，而请求还在跑。
    expect(form.expanded.value).toBe(true)
    expect(api.createUser).toHaveBeenCalledTimes(1)
    settle?.(created)
    await pending
    expect(form.expanded.value).toBe(false)
    scope.stop()
  })

  it('退出登录后清掉密码', () => {
    const { scope, form } = createForm()
    form.password.value = 'a'.repeat(12)

    form.clearSensitiveInput()

    expect(form.password.value).toBe('')
    scope.stop()
  })

  it('销毁状态作用域时清掉密码', () => {
    const { scope, form } = createForm()
    form.password.value = 'a'.repeat(12)

    scope.stop()

    expect(form.password.value).toBe('')
  })
})
