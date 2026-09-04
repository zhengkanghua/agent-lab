import { describe, expect, it } from 'vitest'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '@/shared/model/password'
import { validateCredentials, validatePassword } from '../model/admin-validation'

describe('admin-validation', () => {
  it('两个长度上下界与后端的 Field 约束对齐', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(12)
    expect(PASSWORD_MAX_LENGTH).toBe(128)
  })

  it('通过时返回空串，而不是 null', () => {
    // 调用点直接把返回值当文案渲染（有文案就显示），换成 null 会渲染出 "null"。
    expect(validatePassword('a'.repeat(PASSWORD_MIN_LENGTH))).toBe('')
    expect(validateCredentials('admin@example.com', 'a'.repeat(PASSWORD_MIN_LENGTH))).toBe('')
  })

  it('刚好到边界的密码可以提交', () => {
    expect(validatePassword('a'.repeat(PASSWORD_MIN_LENGTH - 1))).not.toBe('')
    expect(validatePassword('a'.repeat(PASSWORD_MAX_LENGTH))).toBe('')
    expect(validatePassword('a'.repeat(PASSWORD_MAX_LENGTH + 1))).not.toBe('')
  })

  it('邮箱先判、密码后判', () => {
    // 两处都不合法时先报邮箱：从上到下改，不要让人先改了密码再发现邮箱也不对。
    const message = validateCredentials('not-an-email', 'short')
    expect(message).toContain('邮箱')
  })

  it('明显不是邮箱的输入拦住', () => {
    const password = 'a'.repeat(PASSWORD_MIN_LENGTH)
    expect(validateCredentials('', password)).not.toBe('')
    expect(validateCredentials('admin', password)).not.toBe('')
    expect(validateCredentials('admin@example', password)).not.toBe('')
    expect(validateCredentials('admin example@a.com', password)).not.toBe('')
  })

  it('合法但少见的地址放行，判定归后端', () => {
    // 刻意宽松：前端写严会把后端本来会放行的地址挡在外面，而用户看不到这是前端拦的。
    const password = 'a'.repeat(PASSWORD_MIN_LENGTH)
    expect(validateCredentials('admin+lab@example.co.uk', password)).toBe('')
    expect(validateCredentials("o'brien@example.com", password)).toBe('')
    expect(validateCredentials('管理员@例子.中国', password)).toBe('')
  })
})
