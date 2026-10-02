// @vitest-environment node
import { describe, expect, it } from 'vitest'
import type { UserAdminDto } from '@/api/user-admin'
import { sortUsers, summarizeUsers } from '../model/user-account'

function makeUser(overrides: Partial<UserAdminDto> & Pick<UserAdminDto, 'email'>): UserAdminDto {
  return {
    id: `10000000-0000-4000-8000-0000000000${overrides.email.length}`,
    is_active: true,
    is_superuser: false,
    is_verified: true,
    is_environment_admin: false,
    deleted_at: null,
    created_at: '2026-08-18T00:00:00Z',
    updated_at: '2026-08-18T00:00:00Z',
    ...overrides,
  }
}

describe('sortUsers', () => {
  it('环境托管超级用户排在最前，与它的邮箱字典序无关', () => {
    const input = [
      makeUser({ email: 'alice@example.com' }),
      makeUser({ email: 'zoe@example.com', is_environment_admin: true }),
      makeUser({ email: 'bob@example.com' }),
    ]
    const sorted = sortUsers(input)

    expect(sorted.map((user) => user.email)).toEqual([
      'zoe@example.com',
      'alice@example.com',
      'bob@example.com',
    ])
    // 调用点常常拿着渲染中的 users.value，原地排会在渲染途中换顺序。
    expect(input.map((user) => user.email)).toEqual([
      'alice@example.com',
      'zoe@example.com',
      'bob@example.com',
    ])
    expect(sorted).not.toBe(input)
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

  it('已注销的超管不算进「超级用户」', () => {
    // 注销保留 is_superuser（将来若要恢复，恢复出来的权限是对的），所以直接数它会把一个
    // 登不进来的账号算成超级用户，与旁边那个「启用」口径矛盾。
    const stats = summarizeUsers([
      makeUser({ email: 'a@example.com', is_superuser: true }),
      makeUser({
        email: 'b@example.com',
        is_superuser: true,
        is_active: false,
        deleted_at: '2026-08-19T00:00:00Z',
      }),
    ])

    expect(stats).toEqual({ total: 2, active: 1, superusers: 1 })
  })

  it('空列表给出三个零', () => {
    expect(summarizeUsers([])).toEqual({ total: 0, active: 0, superusers: 0 })
  })
})
