<script setup lang="ts">
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'
import { RUN_STATUSES } from '@/api/tasks'
import type { TaskExecutions } from '../composables/useTaskExecutions'
import type { TaskSubmissions } from '../composables/useTaskSubmissions'
import {
  executionStatusLabel,
  formatBeijingTime,
  formatRunStats,
  runStatusLabel,
  taskTypeLabel,
  triggerTypeLabel,
} from '../model/job-copy'
import TaskExecutionDetail from './TaskExecutionDetail.vue'

defineProps<{
  executions: TaskExecutions
  submissions: TaskSubmissions
  taskTypes: ScheduledTaskTypeDto[]
}>()
</script>

<template>
  <section class="executions" aria-label="全部任务执行">
    <form class="lookup" @submit.prevent="executions.selectRun(executions.lookupId.value)">
      <label
        >按执行编号查询<BaseInput
          :model-value="executions.lookupId.value"
          placeholder="完整的任务执行编号"
          @update:model-value="executions.setLookupId"
      /></label>
      <BaseButton type="submit" variant="secondary">查询详情</BaseButton>
    </form>
    <p v-if="executions.lookupError.value" class="error" role="alert">
      {{ executions.lookupError.value }}
    </p>
    <div class="filters">
      <label
        >状态<BaseSelect
          :model-value="executions.status.value"
          @update:model-value="executions.filterStatus"
          ><option value="">全部状态</option>
          <option v-for="status in RUN_STATUSES" :key="status" :value="status">
            {{ runStatusLabel(status) }}
          </option></BaseSelect
        ></label
      >
      <label
        >任务类型<BaseSelect
          :model-value="executions.taskType.value"
          @update:model-value="executions.filterTaskType"
          ><option value="">全部类型</option>
          <option v-for="type in taskTypes" :key="type.task_type" :value="type.task_type">
            {{ taskTypeLabel(type.task_type) }}
          </option></BaseSelect
        ></label
      >
      <BaseButton
        variant="ghost"
        :disabled="executions.list.isFetching.value"
        @click="executions.refresh"
        >刷新</BaseButton
      >
    </div>
    <p class="hint">周期和一次性任务统一展示。时间为北京时间；配置删除后仍可按执行编号查询。</p>
    <p v-if="executions.list.isPending.value" role="status">
      <BaseSpinner :size="18" />正在读取任务执行
    </p>
    <p v-else-if="executions.listError.value" class="error" role="alert">
      {{ executions.listError.value }}
    </p>
    <p v-else-if="!executions.list.data.value?.length" class="empty">
      当前没有符合条件的任务执行。
    </p>
    <ol v-else class="execution-list">
      <li
        v-for="run in executions.list.data.value"
        :key="run.id"
        :class="{ selected: executions.selectedId.value === run.id }"
      >
        <div class="execution-heading">
          <BaseButton
            variant="ghost"
            size="sm"
            :aria-label="`查看执行 ${run.id}`"
            @click="executions.selectRun(run.id)"
            >{{ taskTypeLabel(run.task_type) }}</BaseButton
          >
          <span>{{ executionStatusLabel(run) }}</span>
        </div>
        <p class="hint">
          {{ triggerTypeLabel(run.trigger_type) }} · {{ formatBeijingTime(run.accepted_at) }} · 尝试
          {{ run.attempts }} 次
        </p>
        <p class="run-id">{{ run.id }}</p>
        <p class="run-summary">{{ formatRunStats(run) }}</p>
      </li>
    </ol>
    <nav class="pagination" aria-label="任务执行分页">
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="executions.offset.value === 0 || executions.list.isFetching.value"
        @click="executions.changePage(-1)"
        >上一页</BaseButton
      >
      <span>第 {{ executions.offset.value / executions.pageSize + 1 }} 页</span>
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="
          (executions.list.data.value?.length ?? 0) < executions.pageSize ||
          executions.list.isFetching.value
        "
        @click="executions.changePage(1)"
        >下一页</BaseButton
      >
    </nav>
    <section v-if="executions.selectedId.value" class="detail-section" aria-label="按编号查询结果">
      <p v-if="executions.detail.isPending.value" role="status">
        正在查询 {{ executions.selectedId.value }}
      </p>
      <div v-else-if="executions.detailError.value" class="error" role="alert">
        <p>{{ executions.selectedId.value }}：{{ executions.detailError.value }}</p>
        <BaseButton variant="ghost" size="sm" @click="executions.detail.refetch()"
          >重新查询</BaseButton
        >
      </div>
      <TaskExecutionDetail
        v-else-if="executions.detail.data.value"
        :run="executions.detail.data.value"
        :busy="executions.cancelling.value || !!submissions.busyKey.value"
        :retry-pending="
          submissions.hasPending({ kind: 'retry', runId: executions.selectedId.value })
        "
        @cancel="executions.cancel"
        @open-run="executions.selectRun"
        @retry="
          submissions.submit(
            { kind: 'retry', runId: executions.selectedId.value },
            `重试 ${executions.selectedId.value}`,
          )
        "
      />
      <p v-if="executions.operationError.value" class="error" role="alert">
        {{ executions.operationError.value }}
      </p>
    </section>
  </section>
</template>

<style scoped>
.executions {
  padding-top: var(--space-5);
  font-size: var(--fs-sm);
}
.lookup,
.filters {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: var(--space-3);
}
.lookup label {
  flex: 1;
  min-width: 220px;
}
label {
  display: grid;
  gap: 6px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
.filters {
  margin-top: var(--space-4);
}
.filters label {
  min-width: 160px;
}
.hint {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  margin-top: var(--space-3);
}
.error {
  color: var(--danger);
  margin-top: var(--space-3);
  overflow-wrap: anywhere;
}
.empty {
  padding: var(--space-6) 0;
  color: var(--text-secondary);
}
.execution-list {
  list-style: none;
  margin-top: var(--space-5);
  padding: 0;
  border-top: 1px solid var(--border-subtle);
}
.execution-list li {
  border-bottom: 1px solid var(--border-subtle);
  padding: var(--space-3);
}
.execution-list li.selected {
  background: var(--surface-sunken);
}
.execution-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.execution-list .hint {
  margin-top: 4px;
}
.run-id {
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
  margin-top: 4px;
}
.run-summary {
  margin-top: var(--space-2);
  overflow-wrap: anywhere;
}
.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  margin-top: var(--space-3);
  font-size: var(--fs-xs);
}
.detail-section {
  margin-top: var(--space-4);
}
</style>
