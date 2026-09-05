<script setup lang="ts">
import { computed, ref } from 'vue'
import { History, Pencil, Play, Trash2 } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import type { JobRunDto, ScheduledJobDto, ScheduledJobTaskType } from '@/api/scheduled-jobs'
import type { UseJobFormReturn } from '../composables/useJobForm'
import { formatBeijingTime, formatLastRunSummary, taskTypeLabel } from '../model/job-copy'
import JobForm from './JobForm.vue'
import JobRunHistory from './JobRunHistory.vue'

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
  /** 编辑表单状态（页面持有）；只有当前行是编辑目标时才有值。 */
  editForm: UseJobFormReturn | null
  awaitedRunIds: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{
  'toggle-enabled': [job: ScheduledJobDto, value: boolean]
  'run-now': [job: ScheduledJobDto]
  remove: [job: ScheduledJobDto]
  'toggle-edit': [job: ScheduledJobDto]
  'toggle-history': [job: ScheduledJobDto]
  'submit-edit': [job: ScheduledJobDto]
  'run-finished': [jobId: string, run: JobRunDto]
  /* 编辑表单的字段回写：行内表单是受控组件，字段值归页面的 editForm 持有，
     这里只上报变化——直接改 editForm prop 会触发 vue/no-mutating-props，
     而且会让「谁持有状态」这件事在两层组件里变得含糊。 */
  'update:keyValue': [value: string]
  'update:taskType': [value: ScheduledJobTaskType]
  'update:cronExpr': [value: string]
  'update:limitPerSource': [value: number]
  'update:batchSize': [value: number]
  'update:staleAfterMinutes': [value: number]
  'update:enabled': [value: boolean]
}>()

const confirmingDelete = ref(false)

const isEditOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'edit',
)
const isHistoryOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'history',
)

function onDeleteClick(): void {
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
          @change="emit('toggle-enabled', job, ($event.target as HTMLInputElement).checked)"
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
            {{
              job.next_run_at !== null
                ? formatBeijingTime(job.next_run_at)
                : '未排期（停用或调度器关闭）'
            }}
          </span>
        </p>
      </div>

      <div class="job-actions" role="cell">
        <BaseButton
          variant="ghost"
          size="xs"
          :disabled="busy"
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
            :disabled="busy"
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
          :disabled="busy"
          :aria-label="`删除 ${job.key}`"
          @click="onDeleteClick"
        >
          <template #icon><Trash2 :size="13" aria-hidden="true" /></template>
          删除
        </BaseButton>
      </div>
    </div>

    <BaseCallout v-if="error" class="job-error" tone="danger" :description="error" />

    <JobForm
      v-if="isEditOpen && editForm !== null"
      mode="edit"
      :job="job"
      :key-value="editForm.key.value"
      :task-type="editForm.taskType.value"
      :cron-expr="editForm.cronExpr.value"
      :limit-per-source="editForm.limitPerSource.value"
      :batch-size="editForm.batchSize.value"
      :stale-after-minutes="editForm.staleAfterMinutes.value"
      :enabled="editForm.enabled.value"
      :errors="editForm.errors.value"
      :form-error="editForm.formError.value"
      :submitting="editForm.submitting.value"
      @update:key-value="emit('update:keyValue', $event)"
      @update:task-type="emit('update:taskType', $event)"
      @update:cron-expr="emit('update:cronExpr', $event)"
      @update:limit-per-source="emit('update:limitPerSource', $event)"
      @update:batch-size="emit('update:batchSize', $event)"
      @update:stale-after-minutes="emit('update:staleAfterMinutes', $event)"
      @update:enabled="emit('update:enabled', $event)"
      @submit="emit('submit-edit', job)"
      @close="emit('toggle-edit', job)"
    />

    <JobRunHistory
      v-if="isHistoryOpen"
      :job-id="job.id"
      :active="isHistoryOpen"
      :awaited-run-ids="awaitedRunIds"
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
  font-weight: 680;
  font-size: 0.82rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-type {
  color: var(--text-tertiary);
  font-size: 0.68rem;
  font-weight: 640;
}

.job-cron {
  min-width: 0;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--accent-soft);
  font-family: var(--mono-font);
  font-size: 0.74rem;
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
  font-size: 0.72rem;
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
  font-size: 0.72rem;
}

.schedule-line small {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-size: 0.64rem;
  font-weight: 720;
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
