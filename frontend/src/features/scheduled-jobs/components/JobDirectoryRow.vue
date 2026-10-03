<script setup lang="ts">
import { computed } from 'vue'
import { History, Pencil, Play, Trash2 } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import BaseSwitch from '@/shared/ui/BaseSwitch.vue'
import { requestConfirm } from '@/shared/composables/confirm'
import type { JobRunDto, ScheduledJobDto } from '@/api/scheduled-jobs'
import { formatBeijingTime, formatLastRunSummary, taskTypeLabel } from '../model/job-copy'
import JobRunHistory from './JobRunHistory.vue'
import { supportsJobForm } from '../model/job-validation'

/*
 * 一行定时任务：配置摘要 + 行内操作（立即执行 / 编辑 / 执行历史 / 删除）。
 *
 * 编辑表单与执行历史都展开在这一行下方，整页同时只开一个面板（expanded 判定）；
 * 所有请求都归 useScheduledJobDirectory，本组件不发请求，只把事件原样往上转。
 * 删除的确认归 ConfirmDialog：原来这里是两步按钮（点一下变「确认删除」再点一下），
 * 换成确认框之后能点名删的是哪个 key、说清后果，也与全站其它不可恢复动作同一个形态。
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

const executionPending = computed(
  () => !!props.job.active_run || [...props.awaitedRunIds.values()].includes(props.job.id),
)

const isEditOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'edit',
)
const isHistoryOpen = computed(
  () => props.expanded?.jobId === props.job.id && props.expanded.kind === 'history',
)

async function onDeleteClick(): Promise<void> {
  if (props.busy) return
  const confirmed = await requestConfirm({
    title: `删除任务 ${props.job.key}？`,
    description: '这项配置会被删除，之后不再按周期执行；已经受理的执行继续按当时的快照跑完。',
    confirmLabel: '删除任务',
    tone: 'danger',
  })
  if (confirmed) emit('remove', props.job)
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
        <!-- 开关本体走共享 BaseSwitch：真 checkbox 留在可达性树里，track 只是外观。 -->
        <BaseSwitch
          :checked="job.enabled"
          :disabled="busy"
          :label="`启用 ${job.key}`"
          @change="emit('toggle-enabled', job, $event)"
        />
        <span class="status-chip" :class="job.enabled ? 'is-on' : 'is-off'" role="status">
          {{ job.enabled ? '已启用' : '已停用' }}
        </span>
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
          :disabled="busy || !supportsJobForm(job.task_type)"
          :title="!supportsJobForm(job.task_type) ? '此类型暂不支持编辑' : '编辑之后受理的任务配置'"
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
        <BaseButton
          variant="ghost"
          size="xs"
          class="danger-action"
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

    <!-- 编辑表单收进统一对话框（2026-09 重设计 P4）：展开状态仍是这一行的，
         关闭走表单自己的取消键或 Esc，都汇到 toggle-edit。 -->
    <BaseDialog :open="isEditOpen" :label="`编辑任务 ${job.key}`" @close="emit('toggle-edit', job)">
      <slot v-if="isEditOpen" name="edit" />
    </BaseDialog>

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
  /* 行高 52（2026-09 重设计 P4 的表格规范）。 */
  min-height: 52px;
  padding: 8px 10px;
  /* 悬停换底要和全站同一个节奏。缺了这条，整块底色是瞬变的，
     在一片 150ms 渐变的界面里像少了一帧。 */
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.job-block:focus-within,
.job-row:hover {
  background: var(--surface-sunken);
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
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  cursor: pointer;
}

/* 开关外观归 shared/ui/BaseSwitch.vue，状态胶囊归 styles/components/chip.css。 */

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

/* 删除是危险操作：ghost 底上换 danger 色（盖过 BaseButton ghost 的 accent 悬停），
   并与左边三个中性动作多留一段间距——并排等距时它和「执行历史」一样容易误点。
   这一档只能写在 scoped 里：@layer components 的规则恒定压不过 BaseButton 未分层的
   scoped 悬停（frontend/AGENTS.md 第 6 条），共享层写不出这个效果。 */
.job-actions button.danger-action {
  margin-left: var(--space-2);
  color: var(--danger);
}

.job-actions button.danger-action:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-soft);
}

.job-error {
  margin: 0 10px 12px;
}

/* 与表头同一条断点，且同样按容器宽而不是视口宽（理由见 JobDirectoryTable）。
   容器由上游的 .directory 提供。 */
@container (max-width: 900px) {
  .job-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .job-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}
</style>
