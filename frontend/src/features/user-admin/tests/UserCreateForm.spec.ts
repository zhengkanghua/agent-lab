import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UserCreateForm from '../components/UserCreateForm.vue'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../model/admin-validation'

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
  it('三个输入都是受控值，改动只发事件、不自己留状态', async () => {
    // 密码不能是组件内部 ref：页面在创建成功后要清掉它，而内部 ref 页面碰不到。
    // 这条断言钉住「值从 prop 来、改动往外发」这个方向。
    const wrapper = mountForm()

    await wrapper.get('input[name="new-email"]').setValue('new@example.com')
    await wrapper.get('input[name="new-password"]').setValue('a-long-enough-password')
    await wrapper.get('input[type="checkbox"]').setValue(true)

    expect(wrapper.emitted('update:email')).toEqual([['new@example.com']])
    expect(wrapper.emitted('update:password')).toEqual([['a-long-enough-password']])
    expect(wrapper.emitted('update:superuser')).toEqual([[true]])
  })

  it('渲染的是传入的值，不是内部副本', () => {
    const wrapper = mountForm({
      email: 'shown@example.com',
      password: 'shown-password',
      superuser: true,
    })

    expect(wrapper.get<HTMLInputElement>('input[name="new-email"]').element.value).toBe(
      'shown@example.com',
    )
    expect(wrapper.get<HTMLInputElement>('input[name="new-password"]').element.value).toBe(
      'shown-password',
    )
    expect(wrapper.get<HTMLInputElement>('input[type="checkbox"]').element.checked).toBe(true)
  })

  it('密码框是 password 类型且用 new-password 自动填充，明文不进 DOM 值以外的地方', () => {
    const wrapper = mountForm({ password: 'secret-value' })
    const field = wrapper.get('input[name="new-password"]')

    expect(field.attributes('type')).toBe('password')
    expect(field.attributes('autocomplete')).toBe('new-password')
    // 明文不该出现在渲染文本里（只能在 input 的 value 属性上）。
    expect(wrapper.text()).not.toContain('secret-value')
  })

  it('提交走 submit 事件而不是页面跳转', async () => {
    const wrapper = mountForm({ email: 'a@example.com', password: 'x'.repeat(12) })

    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('提交中时整个表单禁用，关闭按钮也一起', () => {
    const wrapper = mountForm({ submitting: true })

    expect(wrapper.get('input[name="new-email"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('input[name="new-password"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('input[type="checkbox"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('正在创建')
  })

  it('密码位数提示来自校验常量，不写死数字', () => {
    // 写死的话改了校验规则提示就会骗人：说 8 位、实际要 12 位。
    const wrapper = mountForm()

    expect(wrapper.get('input[name="new-password"]').attributes('placeholder')).toBe(
      `${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} 个字符`,
    )
  })

  it('失败原因用 alert 播报', () => {
    const wrapper = mountForm({ error: '该邮箱已存在。' })

    expect(wrapper.get('[role="alert"]').text()).toBe('该邮箱已存在。')
  })

  it('关闭按钮发出 close', async () => {
    const wrapper = mountForm()

    await wrapper.get('button[aria-label="关闭创建账号表单"]').trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
