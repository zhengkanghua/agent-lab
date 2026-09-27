export const userAdminKeys = {
  all: ['user-admin'] as const,
  /**
   * 账号列表的查询键。**参数要进键**：默认口径（不含已注销）与「显示已注销」是两份
   * 不同的数据，共用同一个键的话开关点下去只会命中 10 秒新鲜期里的旧缓存，
   * 表现是「开关像坏了、列表没变」。
   */
  users: (includeDeleted: boolean) => [...userAdminKeys.all, 'users', { includeDeleted }] as const,
}

/**
 * 从列表查询键里读回「有没有带上已注销」。
 *
 * 缓存写入需要按这个标志分派两种处理（默认那份要把注销的行移出、开关那份要把它留在原位），
 * 而 `setQueryData` 的回调只拿得到数据、拿不到键，所以判断键形状的事写在这里一处。
 */
export function includeDeletedOf(queryKey: readonly unknown[]): boolean {
  const params = queryKey[queryKey.length - 1]
  if (typeof params !== 'object' || params === null) return false
  return (params as { includeDeleted?: unknown }).includeDeleted === true
}
