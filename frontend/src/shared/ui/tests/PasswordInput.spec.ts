import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BaseField from '@/shared/ui/BaseField.vue'
import PasswordInput from '@/shared/ui/PasswordInput.vue'

/* 这一份守的是「看着能用、其实静默失效」的两处：
 * 1) 开关点了不换 type——密码框永远是密文或永远是明文，界面都不报错；
 * 2) BaseField 递进来的 id / aria-describedby 落到外层 span 而不是 input 上，
 *    读屏读不到标签和错误原因，页面上完全看不出差别。
 */

describe('PasswordInput', () => {
  it('默认是密文，点开关后转明文，再点回密文', async () => {
    const wrapper = mount(PasswordInput, { props: { modelValue: 'hunter2' } })
    const input = wrapper.find('input')
    expect(input.attributes('type')).toBe('password')

    await wrapper.find('.password-toggle').trigger('click')
    expect(wrapper.find('input').attributes('type')).toBe('text')
    // 明文时输入的仍是同一份值，切换不丢内容。
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('hunter2')

    await wrapper.find('.password-toggle').trigger('click')
    expect(wrapper.find('input').attributes('type')).toBe('password')
  })

  it('开关的 aria 文案随状态切换，且不会提交表单', async () => {
    const wrapper = mount(PasswordInput)
    const toggle = wrapper.find('.password-toggle')
    expect(toggle.attributes('aria-label')).toBe('显示密码')
    // 默认 type 是 button：放在表单里点它不能触发提交。
    expect(toggle.attributes('type')).toBe('button')

    await toggle.trigger('click')
    expect(wrapper.find('.password-toggle').attributes('aria-label')).toBe('隐藏密码')
  })

  it('外部传入的 type 盖不掉密文类型', () => {
    // 密文是默认状态，不能被 attrs 里的 type 顶掉——那等于把密码摊开显示。
    const wrapper = mount(PasswordInput, { attrs: { type: 'text' } })
    expect(wrapper.find('input').attributes('type')).toBe('password')
  })

  it('BaseField 的 aria 接线落在 input 上，不落到外层 span 上', () => {
    const wrapper = mount(
      {
        components: { BaseField, PasswordInput },
        template: `
          <BaseField v-slot="{ control }" label="当前密码" error="密码太短">
            <PasswordInput v-bind="control" name="current-password" />
          </BaseField>
        `,
      },
      { attachTo: document.body },
    )
    const input = wrapper.find('input')
    const span = wrapper.find('.password-input')
    const id = input.attributes('id')
    expect(id).toBeTruthy()
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(input.attributes('aria-describedby')).toBe(`${id}-error`)
    // 外层 span 不该同时带着这些 id/aria：同一个 id 出现两次，读屏会重复播报。
    expect(span.attributes('id')).toBeUndefined()
    expect(span.attributes('aria-describedby')).toBeUndefined()
    // 标签指向真正的输入框。
    expect(wrapper.find('label').attributes('for')).toBe(id)
    wrapper.unmount()
  })

  it('v-model 双向：输入回传新值', async () => {
    const wrapper = mount(PasswordInput, { props: { modelValue: '' } })
    await wrapper.find('input').setValue('new-secret')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['new-secret'])
  })

  it('disabled 同时禁用输入框与开关', () => {
    const wrapper = mount(PasswordInput, { props: { disabled: true } })
    expect(wrapper.find('input').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.password-toggle').attributes('disabled')).toBeDefined()
  })
})
