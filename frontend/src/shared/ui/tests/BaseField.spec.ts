import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import BaseField from '@/shared/ui/BaseField.vue'

/* 重点全在 aria 接线上：那部分原来在每个字段里手写，漏了不报错、
 * 界面也照常，只是读屏用户听不到错误原因。 */

const withInput = {
  default: '<input v-bind="params.control" class="probe" />',
}

/* 从组件本身取 props 类型，不写 Record<string, unknown>：
   后者会把「漏传 label」这种错误一起放过去。 */
type FieldProps = InstanceType<typeof BaseField>['$props']

function mountField(props: FieldProps) {
  return mount(BaseField, {
    props,
    slots: { default: `<template #default="params">${withInput.default}</template>` },
  })
}

describe('BaseField hint 插槽', () => {
  function mountWithHintSlot(props: FieldProps) {
    return mount(BaseField, {
      props,
      slots: {
        default: `<template #default="params">${withInput.default}</template>`,
        hint: '<span class="tone">还可输入 200 字</span>',
      },
    })
  }

  it('插槽内容渲染在说明位，并接上 aria-describedby', () => {
    const wrapper = mountWithHintSlot({ label: '研究内容' })
    const hint = wrapper.get('.field-hint')

    expect(hint.get('.tone').text()).toBe('还可输入 200 字')
    expect(wrapper.get('input.probe').attributes('aria-describedby')).toBe(hint.attributes('id'))
  })

  /* 错误态优先：此刻要念的是「哪里不对」，不是还能输入多少字。 */
  it('有错误时说明位让给错误，指向也跟着换', () => {
    const wrapper = mountWithHintSlot({ label: '研究内容', error: '请输入检索内容' })

    expect(wrapper.find('.field-hint').exists()).toBe(false)
    expect(wrapper.get('input.probe').attributes('aria-describedby')).toBe(
      wrapper.get('.field-error').attributes('id'),
    )
  })
})

describe('BaseField', () => {
  it('label 的 for 指向控件 id', () => {
    const wrapper = mountField({ label: '账号邮箱' })
    const id = wrapper.get('input.probe').attributes('id')
    expect(id).toBeTruthy()
    expect(wrapper.get('label').attributes('for')).toBe(id)
  })

  it('常态下 aria-describedby 指向说明，不标 aria-invalid', () => {
    const wrapper = mountField({ label: '密码', hint: '至少 12 位' })
    const input = wrapper.get('input.probe')
    const hint = wrapper.get('.field-hint')
    expect(input.attributes('aria-describedby')).toBe(hint.attributes('id'))
    expect(input.attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('.field-error').exists()).toBe(false)
  })

  it('错误态下改指错误、标 aria-invalid，并让说明让位', () => {
    const wrapper = mountField({ label: '密码', hint: '至少 12 位', error: '密码太短' })
    const input = wrapper.get('input.probe')
    const error = wrapper.get('.field-error')
    // 两条都念会冲淡重点，此刻用户要听的是「哪里不对」。
    expect(input.attributes('aria-describedby')).toBe(error.attributes('id'))
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(wrapper.find('.field-hint').exists()).toBe(false)
    expect(error.attributes('role')).toBe('alert')
    expect(error.text()).toBe('密码太短')
  })

  it('既无说明也无错误时不写 aria-describedby，避免指向不存在的 id', () => {
    const wrapper = mountField({ label: '标题' })
    expect(wrapper.get('input.probe').attributes('aria-describedby')).toBeUndefined()
  })

  it('required 同时落到控件与视觉标记', () => {
    const wrapper = mountField({ label: '账号邮箱', required: true })
    expect(wrapper.get('input.probe').attributes('required')).toBeDefined()
    expect(wrapper.get('.required-mark').attributes('aria-hidden')).toBe('true')
  })

  it('同一页上的两个字段 id 不相撞', () => {
    // 必须挂在同一个 app 下才是真实场景：useId 的计数器按 app 实例走，
    // 两次独立 mount 都会从头数，测不出同页冲突。
    const host = {
      components: { BaseField },
      template: `
        <form>
          <BaseField label="甲"><template #default="p"><input v-bind="p.control" class="a" /></template></BaseField>
          <BaseField label="乙"><template #default="p"><input v-bind="p.control" class="b" /></template></BaseField>
        </form>
      `,
    }
    const wrapper = mount(host)
    const a = wrapper.get('input.a').attributes('id')
    const b = wrapper.get('input.b').attributes('id')
    expect(a).toBeTruthy()
    expect(a).not.toBe(b)
    expect(wrapper.findAll('label').map((l) => l.attributes('for'))).toEqual([a, b])
  })

  it('外部传入 id 时用它，派生 id 也跟着走', () => {
    const wrapper = mountField({ label: '密码', id: 'login-password', error: '不对' })
    expect(wrapper.get('input.probe').attributes('id')).toBe('login-password')
    expect(wrapper.get('.field-error').attributes('id')).toBe('login-password-error')
  })
})

describe('BaseField 防呆警告（仅开发期）', () => {
  it('传了控件属性却没给默认插槽时警告，避免表单静默失效', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      mount(BaseField, {
        props: { label: '当前密码' },
        attrs: { type: 'password', modelValue: '' },
      })
      expect(warn).toHaveBeenCalledTimes(1)
      expect(String(warn.mock.calls[0]?.[0])).toContain('[BaseField]')
    } finally {
      warn.mockRestore()
    }
  })

  it('给了默认插槽就不警告', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      mountField({ label: '当前密码' })
      expect(warn).not.toHaveBeenCalled()
    } finally {
      warn.mockRestore()
    }
  })
})
