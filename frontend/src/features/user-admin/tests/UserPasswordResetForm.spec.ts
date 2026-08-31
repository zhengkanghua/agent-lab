import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UserPasswordResetForm from '../components/UserPasswordResetForm.vue'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../model/admin-validation'

function mountForm(props: Partial<InstanceType<typeof UserPasswordResetForm>['$props']> = {}) {
  return mount(UserPasswordResetForm, {
    props: {
      email: 'member@example.com',
      password: '',
      error: '',
      submitting: false,
      ...props,
    },
  })
}

describe('UserPasswordResetForm', () => {
  it('标题带上是给谁改密码，避免在多行之间改错人', () => {
    const wrapper = mountForm({ email: 'target@example.com' })

    expect(wrapper.text()).toContain('为 target@example.com 设置新密码')
  })

  it('密码是受控值，改动只发事件', async () => {
    const wrapper = mountForm()

    await wrapper.get('input[name="reset-password"]').setValue('brand-new-password')

    expect(wrapper.emitted('update:password')).toEqual([['brand-new-password']])
  })

  it('密码框是 password 类型，明文不进渲染文本', () => {
    const wrapper = mountForm({ password: 'secret-value' })
    const field = wrapper.get('input[name="reset-password"]')

    expect(field.attributes('type')).toBe('password')
    expect(field.attributes('autocomplete')).toBe('new-password')
    expect(wrapper.text()).not.toContain('secret-value')
  })

  it('提交走 submit 事件，不触发页面跳转', async () => {
    const wrapper = mountForm({ password: 'x'.repeat(12) })

    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('取消按钮发出 cancel，且提交中也能点', async () => {
    // 取消不该被 submitting 禁用：请求卡住时用户至少要能把这个表单收起来。
    const wrapper = mountForm({ submitting: true })

    await wrapper.get('.cancel-command').trigger('click')

    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('提交中禁用输入框', () => {
    const wrapper = mountForm({ submitting: true })

    expect(wrapper.get('input[name="reset-password"]').attributes('disabled')).toBeDefined()
  })

  it('密码位数提示来自校验常量', () => {
    const wrapper = mountForm()

    expect(wrapper.get('input[name="reset-password"]').attributes('placeholder')).toBe(
      `${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`,
    )
  })

  it('失败原因用 alert 播报', () => {
    const wrapper = mountForm({ error: '密码长度不符合要求。' })

    expect(wrapper.get('[role="alert"]').text()).toBe('密码长度不符合要求。')
  })
})
