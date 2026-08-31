import type { UserAdminDto } from '@/api/user-admin'

/*
 * 账号列表的排序与展示口径。纯函数，便于单独断言。
 */

// 提到模块作用域：Intl.DateTimeFormat 的构造开销远高于 format()，账号列表每行都会调用。
const createdAtFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/**
 * 保底管理员置顶，其余按邮箱字典序。
 *
 * 置顶不是审美：它是唯一改不动的一行，排在中间的话管理员会先去点它、发现全是禁用态，
 * 再去找别人。返回新数组，不原地改传入的那个——调用点常常拿着渲染中的 `users.value`。
 */
export function sortUsers(items: UserAdminDto[]): UserAdminDto[] {
  return [...items].sort((left, right) => {
    if (left.is_environment_admin !== right.is_environment_admin) {
      return left.is_environment_admin ? -1 : 1
    }
    return left.email.localeCompare(right.email)
  })
}

export function formatCreatedAt(value: string): string {
  return createdAtFormatter.format(new Date(value))
}

export interface DirectoryStats {
  total: number
  active: number
  superusers: number
}

export function summarizeUsers(items: UserAdminDto[]): DirectoryStats {
  return {
    total: items.length,
    active: items.filter((user) => user.is_active).length,
    superusers: items.filter((user) => user.is_superuser).length,
  }
}
