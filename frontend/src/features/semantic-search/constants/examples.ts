/**
 * 语义检索的示例问题,在空态时引导用户使用。
 *
 * 这些示例覆盖了三种典型检索场景:
 * 1. 政策动态查询(央行利率)
 * 2. 行业趋势追踪(新能源车出口)
 * 3. 经济数据关联(宏观数据与消费)
 */
export const SEARCH_EXAMPLES = [
  '央行近期是否调整利率？',
  '新能源车出口趋势',
  '宏观数据与居民消费',
] as const

export type SearchExample = (typeof SEARCH_EXAMPLES)[number]
