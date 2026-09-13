import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UserCreateForm from '../components/UserCreateForm.vue'

function mountForm(props: Partial<InstanceType<typeof UserCreateForm>['$props']> = {}) {
  return mount(UserCreateForm, {
    props: {
      email: '',
      password: '',
      superuser: false,
      error: '',
      submitting: false,
      ...props,
    },
  })
}

describe('UserCreateForm', () => {
  it('密码使用安全输入框，父级清空后不留内部副本或明文', async () => {
    const wrapper = mountForm({ password: 'secret-value' })
    const field = wrapper.get<HTMLInputElement>('input[name="new-password"]')

    expect(field.attributes('type')).toBe('password')
    expect(field.attributes('autocomplete')).toBe('new-password')
    expect(field.element.value).toBe('secret-value')
    // 明文不该出现在渲染文本里（只能在 input 的 value 属性上）。
    expect(wrapper.text()).not.toContain('secret-value')
    await wrapper.setProps({ password: '' })
    expect(field.element.value).toBe('')
  })

  it('提交中时整个表单禁用，关闭按钮也一起', () => {
    const wrapper = mountForm({ submitting: true })

    expect(wrapper.get('input[name="new-email"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('input[name="new-password"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('input[type="checkbox"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(
      wrapper.get('button[aria-label="关闭创建账号表单"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('失败原因用 alert 播报', () => {
    const wrapper = mountForm({ error: '该邮箱已存在。' })

    expect(wrapper.get('[role="alert"]').text()).toBe('该邮箱已存在。')
  })
})
