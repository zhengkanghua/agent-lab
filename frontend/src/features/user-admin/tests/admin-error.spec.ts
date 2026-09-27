// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { presentAdminError } from '../model/admin-error'

/*
 * 错误码 → 文案表。
 *
 * 这张表自己的注释写着「新增码时两边一起改，漏了这边只会退到兜底文案」——这条用例就是
 * 那个「漏了」的探测器：它断言新码给出的是自己的说明，而不是调用点传进来的兜底句。
 */

const FALLBACK = '账号操作失败，请稍后重试。'

function apiError(code: string): ApiError {
  return new ApiError({ message: code, code, status: 409 })
}

describe('presentAdminError', () => {
  it('停用或改密一个已注销的账号：说的是「已注销」，不是「稍后重试」', () => {
    const message = presentAdminError(apiError('account_already_deleted'), FALLBACK)

    expect(message).toContain('注销')
    expect(message).not.toBe(FALLBACK)
  })

  it('停用或注销自己：说的是「换一个账号」，不是「稍后重试」', () => {
    const message = presentAdminError(apiError('account_self_protected'), FALLBACK)

    expect(message).toContain('当前登录账号')
    expect(message).not.toBe(FALLBACK)
  })

  it('最后一个活跃超管的文案跟着「注销」走，不再说「删除」', () => {
    // 动作已经从删行改成盖注销时间戳，文案还写「删除」会让人去查一条不存在的删除记录。
    const message = presentAdminError(apiError('last_superuser_protected'), FALLBACK)

    expect(message).toContain('注销')
    expect(message).not.toContain('删除')
  })

  it('认不出的码才退到兜底文案', () => {
    expect(presentAdminError(apiError('something_new'), FALLBACK)).toBe(FALLBACK)
  })
})
