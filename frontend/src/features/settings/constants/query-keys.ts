import type { UsageFilters } from '@/api/usage'

/**
 * 用量查询的 Vue Query 键。
 *
 * 筛选条件进键：改模型或改时间范围时，汇总与明细各自的缓存条目都换了一把键，于是两处一起
 * 重新取数——「改动任一个，两处一起变」是键设计的结果，不靠调用方记得同时刷新两处。
 */
export const usageKeys = {
  all: ['usage'] as const,
  summary: (filters: UsageFilters) => [...usageKeys.all, 'summary', filters] as const,
  records: (filters: UsageFilters, offset: number) =>
    [...usageKeys.all, 'records', filters, offset] as const,
  models: () => [...usageKeys.all, 'models'] as const,
}
