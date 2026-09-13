import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'

/** 页面与参数校验测试共用的合成 HTTP 契约样本。 */
export const taskTypes: ScheduledTaskTypeDto[] = [
  {
    task_type: 'freshrss_sync',
    schedulable: true,
    description: '同步新闻',
    defaults: { limit_per_source: 2 },
    params_schema: {
      properties: { limit_per_source: { type: 'integer', minimum: 1, maximum: 100 } },
    },
  },
  {
    task_type: 'index_pending',
    schedulable: true,
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
    schedulable: true,
    description: '数据保留策略：删除指定知识库中超过保留期的旧文档及其向量索引（默认预演）',
    defaults: {
      retention_days: 180,
      dry_run: true,
      knowledge_base_ids: ['10000000-0000-4000-8000-000000000010'],
    },
    params_schema: {
      properties: {
        retention_days: { type: 'integer', minimum: 30, maximum: 730 },
        dry_run: { type: 'boolean' },
        knowledge_base_ids: {
          type: 'array',
          items: { type: 'string', format: 'uuid' },
          minItems: 1,
        },
      },
    },
  },
  {
    task_type: 'pipeline_run_once',
    description: '手动两步连跑',
    schedulable: false,
    defaults: { limit_per_source: 2, batch_size: 20, stale_after_minutes: 60 },
    params_schema: {
      properties: {
        limit_per_source: { type: 'integer', minimum: 1, maximum: 100 },
        batch_size: { type: 'integer', minimum: 1, maximum: 1000 },
        stale_after_minutes: { type: 'integer', minimum: 1, maximum: 10080 },
      },
    },
  },
]
