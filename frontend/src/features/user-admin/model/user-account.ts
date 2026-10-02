import type { UserAdminDto } from '@/api/user-admin'

/*
 * 账号列表的排序与展示口径。纯函数，便于单独断言。
 *
 * 日期显示不在这里：原先本文件自带一个 Intl.DateTimeFormat('zh-CN')，不带 timeZone，
 * 于是它跟随浏览器时区、输出成 2026/08/17 的斜杠形状——正是 shared/model/datetime.ts
 * 开头点名批评的那种写法（同一条记录在不同机器上显示成不同的日子）。账号目录与设置页
 * 显示的是同一份 created_at，两处口径必须一致，所以统一走 datetime.ts 的 formatDate。
 */

/**
 * 环境托管超级用户置顶，其余按邮箱字典序。
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

export interface DirectoryStats {
  total: number
  active: number
  superusers: number
}

/**
 * 概况条的三个数字。
 *
 * 「超级用户」**只数未注销的**：注销保留 ``is_superuser``（将来若要恢复，恢复出来的权限是
 * 对的），所以开关打开时直接数 `is_superuser` 会把一个登不进来的账号算成超级用户，
 * 与旁边那个「启用」口径不一致。
 *
 * 「全部账号」与「启用」跟着列表走：开关打开时它们包含已注销的账号，那是使用者自己要看的结果。
 */
export function summarizeUsers(items: UserAdminDto[]): DirectoryStats {
  return {
    total: items.length,
    active: items.filter((user) => user.is_active).length,
    superusers: items.filter((user) => user.is_superuser && user.deleted_at === null).length,
  }
}
