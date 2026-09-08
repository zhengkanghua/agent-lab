/**
 * 语义检索的示例问题,在空态时引导用户使用。
 *
 * 这些示例覆盖了三种典型检索场景:
 * 1. 政策动态查询(央行利率)
 * 2. 查找操作规定
 * 3. 定位项目资料
 */
export const SEARCH_EXAMPLES = [
  '央行近期是否调整利率？',
  '备份保留期限与恢复步骤',
  '项目验收标准与交付要求',
] as const

export type SearchExample = (typeof SEARCH_EXAMPLES)[number]
