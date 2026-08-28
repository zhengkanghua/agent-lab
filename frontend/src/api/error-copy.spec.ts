import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { resolveErrorCopy } from './error-copy'

const BY_CODE = { user_not_found: '按 code 命中' }
const BY_STATUS = { 404: '按 status 命中' }
const FALLBACK = '兜底'

function apiError(options: { code?: string; status?: number }): ApiError {
  return new ApiError({
    message: 'boom',
    code: options.code ?? 'unknown_error',
    status: options.status,
  })
}

describe('resolveErrorCopy', () => {
  it('prefers the code table over the status table', () => {
    const copy = resolveErrorCopy(apiError({ code: 'user_not_found', status: 404 }), {
      byCode: BY_CODE,
      byStatus: BY_STATUS,
      fallback: FALLBACK,
    })

    expect(copy).toBe('按 code 命中')
  })

  it('falls back to the status table when the code is unknown', () => {
    const copy = resolveErrorCopy(apiError({ code: 'something_new', status: 404 }), {
      byCode: BY_CODE,
      byStatus: BY_STATUS,
      fallback: FALLBACK,
    })

    expect(copy).toBe('按 status 命中')
  })

  it('falls back when neither table matches', () => {
    const copy = resolveErrorCopy(apiError({ code: 'something_new', status: 500 }), {
      byCode: BY_CODE,
      byStatus: BY_STATUS,
      fallback: FALLBACK,
    })

    expect(copy).toBe(FALLBACK)
  })

  // status 缺省时 ApiError 记 0，这个键必须能正常查表，不能被当成「没有状态」。
  it('matches a zero status for transport failures', () => {
    const copy = resolveErrorCopy(apiError({ code: 'network_error' }), {
      byStatus: { 0: '连不上' },
      fallback: FALLBACK,
    })

    expect(copy).toBe('连不上')
  })

  it.each([
    ['a plain Error', new Error('boom')],
    ['a thrown string', 'boom'],
    ['null', null],
  ])('falls back for %s', (_label, cause) => {
    expect(
      resolveErrorCopy(cause, { byCode: BY_CODE, byStatus: BY_STATUS, fallback: FALLBACK }),
    ).toBe(FALLBACK)
  })
})
