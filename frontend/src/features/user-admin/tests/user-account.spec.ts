import { describe, expect, it } from 'vitest'
import type { UserAdminDto } from '@/api/user-admin'
import { formatCreatedAt, sortUsers, summarizeUsers } from '../model/user-account'

function makeUser(overrides: Partial<UserAdminDto> & Pick<UserAdminDto, 'email'>): UserAdminDto {
  return {
    id: `10000000-0000-4000-8000-0000000000${overrides.email.length}`,
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_environment_admin: false,
    created_at: '2026-08-18T00:00:00Z',
    updated_at: '2026-08-18T00:00:00Z',
    ...overrides,
  }
}

describe('sortUsers', () => {
  it('保底管理员排在最前，与它的邮箱字典序无关', () => {
    const sorted = sortUsers([
      makeUser({ email: 'alice@example.com' }),
      makeUser({ email: 'zoe@example.com', is_environment_admin: true }),
      makeUser({ email: 'bob@example.com' }),
    ])

    expect(sorted.map((user) => user.email)).toEqual([
      'zoe@example.com',
      'alice@example.com',
      'bob@example.com',
    ])
  })

  it('不原地改传入的数组', () => {
    // 调用点常常拿着渲染中的 users.value，原地排会在渲染途中换顺序。
    const input = [makeUser({ email: 'b@example.com' }), makeUser({ email: 'a@example.com' })]
    const sorted = sortUsers(input)

    expect(input.map((user) => user.email)).toEqual(['b@example.com', 'a@example.com'])
    expect(sorted).not.toBe(input)
  })

  it('多个保底管理员之间仍按邮箱排', () => {
    const sorted = sortUsers([
      makeUser({ email: 'ops@example.com', is_environment_admin: true }),
      makeUser({ email: 'admin@example.com', is_environment_admin: true }),
    ])

    expect(sorted.map((user) => user.email)).toEqual(['admin@example.com', 'ops@example.com'])
  })
})

describe('summarizeUsers', () => {
  it('分别数总数、启用数与管理员数', () => {
    const stats = summarizeUsers([
      makeUser({ email: 'a@example.com' }),
      makeUser({ email: 'b@example.com', is_active: false }),
      makeUser({ email: 'c@example.com', is_superuser: true }),
      makeUser({ email: 'd@example.com', is_active: false, is_superuser: true }),
    ])

    // 三个数各自独立计数：停用的管理员同时算进 superusers、不算进 active。
    expect(stats).toEqual({ total: 4, active: 2, superusers: 2 })
  })

  it('空列表给出三个零', () => {
    expect(summarizeUsers([])).toEqual({ total: 0, active: 0, superusers: 0 })
  })
})

describe('formatCreatedAt', () => {
  it('把后端的 ISO 时间戳格式化成年月日', () => {
    expect(formatCreatedAt('2026-08-18T12:34:56Z')).toMatch(/2026/)
    expect(formatCreatedAt('2026-08-18T12:34:56Z')).not.toMatch(/12:34/)
  })
})
