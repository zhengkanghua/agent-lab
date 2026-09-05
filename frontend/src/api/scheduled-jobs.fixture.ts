import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'

/** 页面与参数校验测试共用的合成 HTTP 契约样本。 */
export const taskTypes: ScheduledTaskTypeDto[] = [
  {
    task_type: 'freshrss_sync',
    description: '同步新闻',
    defaults: { limit_per_source: 2 },
    params_schema: {
      properties: { limit_per_source: { type: 'integer', minimum: 1, maximum: 100 } },
    },
  },
  {
    task_type: 'index_pending',
    description: '索引新闻',
    defaults: { batch_size: 20, stale_after_minutes: 60 },
    params_schema: {
      properties: {
        batch_size: { type: 'integer', minimum: 1, maximum: 1000 },
        stale_after_minutes: { type: 'integer', minimum: 1, maximum: 10080 },
      },
    },
  },
  {
    task_type: 'prune_old_documents',
    description: '清理新闻',
    defaults: { retention_days: 180, dry_run: true },
    params_schema: {
      properties: {
        retention_days: { type: 'integer', minimum: 30, maximum: 730 },
        dry_run: { type: 'boolean' },
      },
    },
  },
]
