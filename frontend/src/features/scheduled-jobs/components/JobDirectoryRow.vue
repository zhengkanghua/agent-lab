<script setup lang="ts">
import { computed, ref } from 'vue'
import { History, Pencil, Play, Trash2 } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import type { JobRunDto, ScheduledJobDto } from '@/api/scheduled-jobs'
import { formatBeijingTime, formatLastRunSummary, taskTypeLabel } from '../model/job-copy'
import JobRunHistory from './JobRunHistory.vue'
import { supportsJobForm } from '../model/job-validation'

/*
 * 一行定时任务：配置摘要 + 行内操作（立即执行 / 编辑 / 执行历史 / 删除）。
 *
 * 编辑表单与执行历史都展开在这一行下方，整页同时只开一个面板（expanded 判定）；
 * 所有请求都归 useScheduledJobDirectory，本组件不发请求，只把事件原样往上转。
 * 删除是两步确认：第一次点变成「确认删除」，再点才真正发请求。
 */

const props = defineProps<{
  job: ScheduledJobDto
  busy: boolean
  error: string
  expanded: { jobId: string; kind: 'edit' | 'history' } | null
  awaitedRunIds: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{
  'toggle-enabled': [job: ScheduledJobDto, value: boolean]
  'run-now': [job: ScheduledJobDto]
  remove: [job: ScheduledJobDto]
  'toggle-edit': [job: ScheduledJobDto]
  'toggle-history': [job: ScheduledJobDto]
  'run-finished': [jobId: string, run: JobRunDto]
}>()

const confirmingDelete = ref(false)
const executionPending = computed(
  () => !!props.job.active_run || [...props.awaitedRunIds.values()].includes(props.job.id),
)

const isEditOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'edit',
)
const isHistoryOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'history',
)

function toggleEnabled(event: Event): void {
  const input = event.target as HTMLInputElement
  const requested = input.checked
  input.checked = props.job.enabled
  emit('toggle-enabled', props.job, requested)
}

function onDeleteClick(): void {
  if (props.busy || executionPending.value) return
  if (!confirmingDelete.value) {
    confirmingDelete.value = true
    return
  }
  confirmingDelete.value = false
  emit('remove', props.job)
}
</script>

<template>
  <div class="job-block" role="row">
    <div class="job-row" role="presentation">
      <div class="job-identity" role="cell">
        <span class="job-key" :title="job.key">{{ job.key }}</span>
        <span class="job-type">{{ taskTypeLabel(job.task_type) }}</span>
      </div>

      <code class="job-cron" role="cell" :title="job.cron_expr">{{ job.cron_expr }}</code>

      <label class="job-toggle" role="cell">
        <input
          type="checkbox"
          :checked="job.enabled"
          :disabled="busy"
          :aria-label="`启用 ${job.key}`"
          @change="toggleEnabled"
        />
        <span>{{ job.enabled ? '已启用' : '已停用' }}</span>
      </label>

      <div class="job-schedule" role="cell">
        <p class="schedule-line">
          <small>上次</small>
          <span :data-status="job.last_run?.status ?? 'none'">
            {{ formatLastRunSummary(job) }}
          </span>
        </p>
        <p class="schedule-line">
          <small>下次</small>
          <span>
            {{ job.next_run_at !== null ? formatBeijingTime(job.next_run_at) : '未排期' }}
          </span>
        </p>
      </div>

      <div class="job-actions" role="cell">
        <BaseButton
          variant="ghost"
          size="xs"
          :disabled="busy || executionPending"
          :aria-label="`立即执行 ${job.key}`"
          @click="emit('run-now', job)"
        >
          <template #icon><Play :size="13" aria-hidden="true" /></template>
          立即执行
        </BaseButton>
        <BaseButton
          variant="ghost"
          size="xs"
          :aria-pressed="isEditOpen"
          :disabled="busy || job.enabled || executionPending || !supportsJobForm(job.task_type)"
          :title="
            !supportsJobForm(job.task_type)
              ? '此类型暂不支持编辑'
              : job.enabled || job.active_run
                ? '先停用，并等待当前任务执行结束'
                : '编辑任务配置'
          "
          :aria-label="`编辑 ${job.key}`"
          @click="emit('toggle-edit', job)"
        >
          <template #icon><Pencil :size="13" aria-hidden="true" /></template>
          编辑
        </BaseButton>
        <BaseButton
          variant="ghost"
          size="xs"
          :aria-pressed="isHistoryOpen"
          :aria-label="`查看 ${job.key} 的执行历史`"
          @click="emit('toggle-history', job)"
        >
          <template #icon><History :size="13" aria-hidden="true" /></template>
          执行历史
        </BaseButton>
        <span v-if="confirmingDelete" class="delete-confirm">
          <BaseButton
            variant="ghost"
            size="xs"
            :disabled="busy || executionPending"
            :aria-label="`确认删除 ${job.key}`"
            @click="onDeleteClick"
          >
            <template #icon><Trash2 :size="13" aria-hidden="true" /></template>
            确认删除
          </BaseButton>
          <BaseButton variant="ghost" size="xs" :disabled="busy" @click="confirmingDelete = false">
            取消
          </BaseButton>
        </span>
        <BaseButton
          v-else
          variant="ghost"
          size="xs"
          :disabled="busy || executionPending"
          :aria-label="`删除 ${job.key}`"
          @click="onDeleteClick"
        >
          <template #icon><Trash2 :size="13" aria-hidden="true" /></template>
          删除
        </BaseButton>
      </div>
    </div>

    <BaseCallout v-if="error" class="job-error" tone="danger" :description="error" />

    <slot v-if="isEditOpen && !job.enabled && !executionPending" name="edit" />

    <JobRunHistory
      v-if="isHistoryOpen"
      :job-id="job.id"
      :active="isHistoryOpen"
      :awaited-run-ids="awaitedRunIds"
      :active-run="job.active_run ?? null"
      @awaited-finished="(run) => emit('run-finished', job.id, run)"
    />
  </div>
</template>

<style scoped>
.job-block {
  border-bottom: 1px solid var(--border-subtle);
}

.job-block:last-child {
  border-bottom: 0;
}

.job-row {
  display: grid;
  grid-template-columns: var(
    --job-row-columns,
    minmax(150px, 1.1fr) minmax(96px, 0.7fr) minmax(96px, 0.6fr) minmax(210px, 1.3fr)
      minmax(245px, 1.3fr)
  );
  align-items: center;
  gap: 18px;
  padding: 14px 10px;
}

.job-block:focus-within,
.job-row:hover {
  background: var(--surface-hover);
}

.job-identity {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.job-key {
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-type {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.job-cron {
  min-width: 0;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--accent-soft);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-toggle {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  cursor: pointer;
}

.job-toggle input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}

.job-schedule {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.schedule-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: var(--fs-xs);
}

.schedule-line small {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
}

.schedule-line span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
}

.schedule-line span[data-status='succeeded'] {
  color: var(--accent);
}

.schedule-line span[data-status='failed'] {
  color: var(--danger);
}

.job-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.delete-confirm {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.job-error {
  margin: 0 10px 12px;
}

@media (max-width: 1080px) {
  .job-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .job-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}
</style>
