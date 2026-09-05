<script setup lang="ts">
import { CalendarClock, RefreshCw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { JobRunDto, ScheduledJobDto, ScheduledJobTaskType } from '@/api/scheduled-jobs'
import type { ExpandedPanel, ScheduledJobLoadState } from '../composables/useScheduledJobDirectory'
import type { UseJobFormReturn } from '../composables/useJobForm'
import JobDirectoryRow from './JobDirectoryRow.vue'

/*
 * 定时任务目录：标题、刷新键、三种非就绪态，以及就绪后的任务列表。
 * 行内控件都在 JobDirectoryRow 里，这里只把事件原样往上转——所有请求都归
 * useScheduledJobDirectory，中间这一层不自己发请求。
 */

const props = defineProps<{
  jobs: ScheduledJobDto[]
  loadState: ScheduledJobLoadState
  loadError: string
  busyJobIds: ReadonlySet<string>
  rowErrors: Readonly<Record<string, string>>
  expanded: ExpandedPanel | null
  /** 编辑表单状态（页面持有）；展开编辑的行会拿到它。 */
  editForm: UseJobFormReturn | null
  awaitedRunIds: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{
  refresh: []
  'toggle-enabled': [job: ScheduledJobDto, value: boolean]
  'run-now': [job: ScheduledJobDto]
  remove: [job: ScheduledJobDto]
  'toggle-edit': [job: ScheduledJobDto]
  'toggle-history': [job: ScheduledJobDto]
  'submit-edit': [job: ScheduledJobDto]
  'run-finished': [jobId: string, run: JobRunDto]
  /* 编辑表单字段回写的逐层转发，理由见 JobDirectoryRow 的 emits 注释。 */
  'update:keyValue': [value: string]
  'update:taskType': [value: ScheduledJobTaskType]
  'update:cronExpr': [value: string]
  'update:limitPerSource': [value: number]
  'update:batchSize': [value: number]
  'update:staleAfterMinutes': [value: number]
  'update:enabled': [value: boolean]
}>()

function editFormFor(job: ScheduledJobDto): UseJobFormReturn | null {
  return props.expanded?.jobId === job.id && props.expanded.kind === 'edit' ? props.editForm : null
}
</script>

<template>
  <section class="directory" aria-labelledby="directory-title" style="container-type: inline-size">
    <div class="directory-heading">
      <div>
        <p>任务目录</p>
        <h2 id="directory-title">当前的定时任务</h2>
      </div>
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="loadState === 'loading'"
        @click="emit('refresh')"
      >
        <template #icon>
          <RefreshCw :class="{ spin: loadState === 'loading' }" :size="15" aria-hidden="true" />
        </template>
        刷新
      </BaseButton>
    </div>

    <div v-if="loadState === 'loading'" class="directory-state" role="status">
      <BaseSpinner :size="20" />
      正在读取定时任务
    </div>

    <div
      v-else-if="loadState === 'error'"
      class="directory-state directory-state-error"
      role="alert"
    >
      <span>{{ loadError }}</span>
      <BaseButton variant="ghost" size="xs" @click="emit('refresh')">重新加载</BaseButton>
    </div>

    <div v-else-if="jobs.length === 0" class="directory-state">
      <CalendarClock :size="21" aria-hidden="true" />
      当前还没有定时任务，用右上角的「新建任务」创建一个。
    </div>

    <div v-else class="job-table" role="table" aria-label="定时任务列表">
      <div class="job-table-head" role="row">
        <span role="columnheader">任务</span>
        <span role="columnheader">节奏</span>
        <span role="columnheader">启停</span>
        <span role="columnheader">执行状态</span>
        <span role="columnheader" class="align-center">操作</span>
      </div>

      <JobDirectoryRow
        v-for="job in jobs"
        :key="job.id"
        :job="job"
        :busy="busyJobIds.has(job.id)"
        :error="rowErrors[job.id] ?? ''"
        :expanded="expanded"
        :edit-form="editFormFor(job)"
        :awaited-run-ids="awaitedRunIds"
        @toggle-enabled="(job, value) => emit('toggle-enabled', job, value)"
        @run-now="emit('run-now', $event)"
        @remove="emit('remove', $event)"
        @toggle-edit="emit('toggle-edit', $event)"
        @toggle-history="emit('toggle-history', $event)"
        @submit-edit="emit('submit-edit', $event)"
        @run-finished="(jobId, run) => emit('run-finished', jobId, run)"
        @update:key-value="emit('update:keyValue', $event)"
        @update:task-type="emit('update:taskType', $event)"
        @update:cron-expr="emit('update:cronExpr', $event)"
        @update:limit-per-source="emit('update:limitPerSource', $event)"
        @update:batch-size="emit('update:batchSize', $event)"
        @update:stale-after-minutes="emit('update:staleAfterMinutes', $event)"
        @update:enabled="emit('update:enabled', $event)"
      />
    </div>
  </section>
</template>

<style scoped>
.directory {
  margin-top: 36px;
}

.directory-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 16px;
}

.directory-heading p {
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 760;
}

.directory-heading h2 {
  margin-top: 4px;
  font-size: 1.1rem;
  font-weight: 760;
}

/* 刷新键走 BaseButton（ghost），与账号目录、执行历史同款；转动用共享 .spin，
   不再私有一份 keyframes。 */

.directory-state {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 26px 12px;
  color: var(--text-tertiary);
  font-size: 0.8rem;
}

.directory-state-error {
  color: var(--danger);
}

.job-table {
  /* 列宽分配跟着内容走：执行状态是两行文案的主内容列，多给；操作列收紧到
     按钮组的实际宽度（250px），让按钮组贴着自己的列居中、不再悬在大片空白
     右侧（2026-09 审查的「操作列居右」观感来自最后一列过宽 + 前列留白）。 */
  --job-row-columns: minmax(150px, 1.1fr) minmax(96px, 0.7fr) minmax(96px, 0.6fr)
    minmax(210px, 1.5fr) minmax(250px, 1.1fr);
  border-top: 1px solid var(--border-subtle);
}

.job-table-head {
  display: grid;
  grid-template-columns: var(--job-row-columns);
  gap: 18px;
  padding: 10px;
  color: var(--text-tertiary);
  font-size: 0.66rem;
  font-weight: 720;
  letter-spacing: 0.04em;
}

.align-right {
  text-align: right;
}

.align-center {
  text-align: center;
}

@media (max-width: 1080px) {
  .job-table-head {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .job-table-head span:nth-child(n + 3) {
    display: none;
  }
}
</style>
