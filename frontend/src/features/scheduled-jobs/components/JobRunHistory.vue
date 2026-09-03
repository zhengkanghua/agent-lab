<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from '@lucide/vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { JobRunDto } from '@/api/scheduled-jobs'
import { useJobRuns } from '../composables/useJobRuns'
import {
  formatBeijingTime,
  formatRunStats,
  runStatusLabel,
  triggerTypeLabel,
} from '../model/job-copy'

/*
 * 单个任务的执行历史面板（Q2/Q3 共识）：最近 20 条、新→旧；面板打开且有执行在途时
 * 每 5 秒轮询，全部落定后自动停。统计摘要与时间格式化都在 model/job-copy。
 */

const props = defineProps<{
  jobId: string
  /** 面板是否展开；收起时停止查询与轮询。 */
  active: boolean
  /** 手动触发后等待终态的 run id → 任务 id（目录持有）。 */
  awaitedRunIds: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{
  /** 被跟踪的手动触发进入终态。 */
  'awaited-finished': [run: JobRunDto]
}>()

const awaitedForJob = computed(() => {
  const ids = new Set<string>()
  for (const [runId, ownerJobId] of props.awaitedRunIds) {
    if (ownerJobId === props.jobId) ids.add(runId)
  }
  return ids
})

const query = useJobRuns({
  jobId: computed(() => props.jobId),
  enabled: computed(() => props.active),
  hasAwaitedRun: computed(() => awaitedForJob.value.size > 0),
  isAwaited: (runId: string) => awaitedForJob.value.has(runId),
  onAwaitedFinished: (run) => emit('awaited-finished', run),
})

const runs = computed(() => query.data.value ?? [])

function refresh(): void {
  void query.refetch()
}
</script>

<template>
  <section class="run-history" aria-label="执行历史">
    <div class="history-heading">
      <p>执行历史（最近 20 条）</p>
      <button
        class="refresh-button"
        type="button"
        :disabled="query.isFetching.value"
        @click="refresh"
      >
        <RefreshCw :class="{ spin: query.isFetching.value }" :size="14" aria-hidden="true" />
        刷新
      </button>
    </div>

    <div v-if="query.isPending.value" class="history-state" role="status">
      <BaseSpinner :size="18" />
      正在读取执行历史
    </div>
    <div v-else-if="query.isError.value" class="history-state history-state-error" role="alert">
      执行历史读取失败，请稍后刷新重试。
    </div>
    <p v-else-if="runs.length === 0" class="history-state">这个任务还没有执行记录。</p>

    <ol v-else class="run-list">
      <li v-for="run in runs" :key="run.id" class="run-item" :data-status="run.status">
        <div class="run-meta">
          <span class="run-badge" :data-status="run.status">
            {{ runStatusLabel(run.status) }}
          </span>
          <span class="run-trigger">{{ triggerTypeLabel(run.trigger_type) }}</span>
          <time :datetime="run.started_at">{{ formatBeijingTime(run.started_at) }}</time>
          <span v-if="run.error_type" class="run-error-type" :title="run.error_type">
            {{ run.error_type }}
          </span>
        </div>
        <p class="run-stats">{{ formatRunStats(run) }}</p>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.run-history {
  margin-top: 14px;
  padding: 16px 18px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
}

.history-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.history-heading p {
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 760;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
}

.refresh-button:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

.refresh-button .spin {
  animation: run-spin 900ms linear infinite;
}

@keyframes run-spin {
  to {
    transform: rotate(360deg);
  }
}

.history-state {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: 13px;
  color: var(--text-tertiary);
  font-size: 0.76rem;
}

.history-state-error {
  color: var(--danger);
}

.run-list {
  display: grid;
  gap: 0;
  margin: 13px 0 0;
  padding: 0;
  list-style: none;
}

.run-item {
  display: grid;
  gap: 4px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 0.75rem;
}

.run-item:last-child {
  border-bottom: 0;
}

.run-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  color: var(--text-secondary);
}

.run-badge {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 720;
  color: var(--text-secondary);
  background: var(--surface-hover);
}

.run-badge[data-status='succeeded'] {
  color: var(--accent);
  background: var(--accent-soft);
}

.run-badge[data-status='failed'] {
  color: var(--danger);
  background: var(--danger-soft);
}

.run-badge[data-status='running'] {
  color: var(--text-primary);
}

.run-trigger {
  font-size: 0.68rem;
  font-weight: 640;
}

.run-error-type {
  margin-left: auto;
  color: var(--danger);
  font-family: var(--mono-font);
  font-size: 0.68rem;
}

.run-stats {
  color: var(--text-tertiary);
  font-size: 0.71rem;
}
</style>
