import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
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

function mountHarness() {
  const onCreated = vi.fn()
  const onOpen = vi.fn()
  let composable: ReturnType<typeof useAccountCreateForm> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useAccountCreateForm({ onCreated, onOpen })
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, form: composable, onCreated, onOpen }
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

  it('展开时通知页面清掉上一条成功提示', () => {
    const { wrapper, form, onOpen } = mountHarness()

    form.open()

    // 那条提示说的是上一个账号，留着会被当成这一次的结果。
    expect(form.expanded.value).toBe(true)
    expect(onOpen).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('本地校验不过就不发请求', async () => {
    const { wrapper, form, onCreated } = mountHarness()
    form.open()
    form.email.value = 'not-an-email'
    form.password.value = 'a'.repeat(12)

    await form.submit()

    expect(api.createUser).not.toHaveBeenCalled()
    expect(form.error.value).not.toBe('')
    expect(onCreated).not.toHaveBeenCalled()
    expect(form.expanded.value).toBe(true)
    wrapper.unmount()
  })

  it('提交前去掉邮箱两端的空白', async () => {
    api.createUser.mockResolvedValue(created)
    const { wrapper, form } = mountHarness()
    form.open()
    form.email.value = '  reader@example.com  '
    form.password.value = 'a'.repeat(12)

    await form.submit()

    expect(api.createUser).toHaveBeenCalledWith({
      email: 'reader@example.com',
      password: 'a'.repeat(12),
      isSuperuser: false,
    })
    wrapper.unmount()
  })

  it('创建成功后收起表单、清空输入、把新行交出去', async () => {
    api.createUser.mockResolvedValue(created)
    const { wrapper, form, onCreated } = mountHarness()
    form.open()
    fill(form)
    form.superuser.value = true

    await form.submit()

    expect(form.expanded.value).toBe(false)
    expect(form.email.value).toBe('')
    expect(form.password.value).toBe('')
    expect(form.superuser.value).toBe(false)
    expect(onCreated).toHaveBeenCalledWith(created)
    wrapper.unmount()
  })

  it('创建失败时表单留着、输入不丢', async () => {
    api.createUser.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'email_already_exists', status: 409 }),
    )
    const { wrapper, form, onCreated } = mountHarness()
    form.open()
    fill(form)

    await form.submit()

    expect(form.expanded.value).toBe(true)
    expect(form.email.value).toBe('reader@example.com')
    expect(form.error.value).not.toBe('')
    expect(form.submitting.value).toBe(false)
    expect(onCreated).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('提交中不许关、也不许再提交一次', async () => {
    let settle: ((value: UserAdminDto) => void) | undefined
    api.createUser.mockImplementation(() => new Promise((resolve) => (settle = resolve)))
    const { wrapper, form } = mountHarness()
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
    wrapper.unmount()
  })

  it('退出登录后清掉密码', () => {
    const { wrapper, form } = mountHarness()
    form.password.value = 'a'.repeat(12)

    form.clearSensitiveInput()

    expect(form.password.value).toBe('')
    wrapper.unmount()
  })

  it('卸载时清掉密码', () => {
    const { wrapper, form } = mountHarness()
    form.password.value = 'a'.repeat(12)

    wrapper.unmount()

    expect(form.password.value).toBe('')
  })
})
