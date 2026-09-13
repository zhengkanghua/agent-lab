<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Check, Plus, RefreshCw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseDialog from '@/shared/ui/BaseDialog.vue'
import { authSession } from '@/features/auth'
import { listKnowledgeBases, type KnowledgeBaseDto } from '@/api/knowledge-bases'
import {
  JobDirectoryTable,
  JobForm,
  useJobForm,
  useScheduledJobDirectory,
  useTaskExecutions,
  useTaskSubmissions,
  TaskExecutionView,
  TaskSubmissionNotice,
  TaskPolicyPanel,
  PipelineSubmissionForm,
} from '@/features/scheduled-jobs'
import type { PipelineRequest } from '@/api/tasks'

/* 定时任务管理页：作为 /admin 的子路由渲染在 AdminShell 的内容区里。
 * 侧边栏、顶部标题栏、退出登录都由 AdminShell 提供；本页只负责正文内容。
 * 创建与编辑共用 JobForm（受控字段归 useJobForm），行内操作归 useScheduledJobDirectory。 */

const accountId = authSession.user.value?.id ?? 'anonymous'
const executions = useTaskExecutions(accountId)
const submissions = useTaskSubmissions(accountId, (receipt) => executions.selectRun(receipt.run_id))
const directory = useScheduledJobDirectory({ accountId, submissions })
const policyOpen = ref(false)
const pipelineOpen = ref(false)

async function submitPipeline(params: PipelineRequest): Promise<void> {
  pipelineOpen.value = false
  await submissions.submit({ kind: 'pipeline', params }, '手动 Pipeline')
}

/* 维护清理允许显式选择停用库，选项必须覆盖任务中已有的全部范围。 */
const knowledgeBaseOptions = ref<KnowledgeBaseDto[]>([])
const knowledgeBaseError = ref('')

async function loadKnowledgeBases(): Promise<void> {
  try {
    knowledgeBaseOptions.value = await listKnowledgeBases(true)
    knowledgeBaseError.value = ''
  } catch {
    knowledgeBaseError.value = '知识库选项加载失败，清理范围暂不可选。'
  }
}

const createPanel = ref(false)

const createForm = useJobForm({
  mode: 'create',
  job: null,
  taskTypes: directory.taskTypes,
  onSubmit: async (payload) => {
    await directory.createJob(payload)
    createPanel.value = false
    createForm.reset()
  },
  onClose: () => {
    createPanel.value = false
  },
})

/* 编辑表单是页面级单例：展开哪一行，字段就重置成哪一行的配置（useJobForm 内部 watch）。 */
const editForm = useJobForm({
  mode: 'edit',
  job: directory.editingJob,
  taskTypes: directory.taskTypes,
  onSubmit: async (payload) => {
    const job = directory.editingJob.value
    if (job !== null) await directory.updateJob(job, payload)
  },
  onClose: () => {
    directory.closePanel()
  },
})

onMounted(() => {
  directory.load()
  void loadKnowledgeBases()
})

function openCreate(): void {
  createPanel.value = true
  directory.clearFeedback()
}
</script>

<template>
  <section class="admin-page" aria-labelledby="admin-title" style="container-type: inline-size">
    <div class="page-bar">
      <nav class="task-views" aria-label="任务管理视图">
        <BaseButton
          :variant="executions.view.value === 'configurations' ? 'secondary' : 'ghost'"
          :aria-pressed="executions.view.value === 'configurations'"
          @click="executions.setView('configurations')"
          >周期配置</BaseButton
        >
        <BaseButton
          :variant="executions.view.value === 'executions' ? 'secondary' : 'ghost'"
          :aria-pressed="executions.view.value === 'executions'"
          @click="executions.setView('executions')"
          >任务执行</BaseButton
        >
      </nav>
      <div class="page-actions">
        <BaseButton variant="ghost" @click="policyOpen = true">默认策略</BaseButton>
        <BaseButton
          v-if="executions.view.value === 'executions'"
          variant="primary"
          @click="pipelineOpen = true"
          >手动 Pipeline</BaseButton
        >
        <BaseButton
          v-if="executions.view.value === 'configurations' && !createPanel"
          variant="primary"
          :disabled="directory.taskTypes.value.length === 0"
          @click="openCreate"
        >
          <template #icon><Plus :size="18" aria-hidden="true" /></template>
          新建任务
        </BaseButton>
      </div>
    </div>

    <TaskSubmissionNotice :submissions="submissions" @open-run="executions.selectRun" />
    <BaseDialog :open="policyOpen" label="任务默认策略" @close="policyOpen = false">
      <TaskPolicyPanel v-if="policyOpen" @close="policyOpen = false" />
    </BaseDialog>
    <BaseDialog :open="pipelineOpen" label="手动 Pipeline" @close="pipelineOpen = false">
      <PipelineSubmissionForm
        v-if="pipelineOpen"
        :spec="directory.taskTypes.value.find((item) => item.task_type === 'pipeline_run_once')"
        :busy="!!submissions.busyKey.value"
        :pending="submissions.pending.value.some((item) => item.command.kind === 'pipeline')"
        @submit="submitPipeline"
        @close="pipelineOpen = false"
      />
    </BaseDialog>

    <TaskExecutionView
      v-if="executions.view.value === 'executions'"
      :executions="executions"
      :submissions="submissions"
      :task-types="directory.taskTypes.value"
    />

    <div v-show="executions.view.value === 'configurations'">
      <div v-if="directory.taskTypesError.value" role="alert">
        {{ directory.taskTypesError.value }}
        <BaseButton variant="ghost" @click="directory.load">
          <template #icon><RefreshCw :size="16" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </div>

      <p v-if="knowledgeBaseError" role="alert">
        {{ knowledgeBaseError }}
        <BaseButton variant="ghost" @click="loadKnowledgeBases">
          <template #icon><RefreshCw :size="16" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </p>

      <JobForm
        v-if="createPanel"
        mode="create"
        :job="null"
        :key-value="createForm.key.value"
        :task-type="createForm.taskType.value"
        :cron-expr="createForm.cronExpr.value"
        :limit-per-source="createForm.limitPerSource.value"
        :batch-size="createForm.batchSize.value"
        :stale-after-minutes="createForm.staleAfterMinutes.value"
        :retention-days="createForm.retentionDays.value"
        :dry-run="createForm.dryRun.value"
        :knowledge-base-ids="createForm.knowledgeBaseIds.value"
        :knowledge-base-options="knowledgeBaseOptions"
        :task-types="directory.taskTypes.value"
        :enabled="createForm.enabled.value"
        :errors="createForm.errors.value"
        :form-error="createForm.formError.value"
        :submitting="createForm.submitting.value"
        @update:key-value="createForm.key.value = $event"
        @update:task-type="createForm.setTaskType($event)"
        @update:cron-expr="createForm.cronExpr.value = $event"
        @update:limit-per-source="createForm.limitPerSource.value = $event"
        @update:batch-size="createForm.batchSize.value = $event"
        @update:stale-after-minutes="createForm.staleAfterMinutes.value = $event"
        @update:retention-days="createForm.retentionDays.value = $event"
        @update:dry-run="createForm.dryRun.value = $event"
        @update:knowledge-base-ids="createForm.knowledgeBaseIds.value = $event"
        @update:enabled="createForm.enabled.value = $event"
        @submit="createForm.submit()"
        @close="createForm.close()"
      />

      <p v-if="directory.feedback.value" class="feedback" role="status">
        <Check :size="16" aria-hidden="true" />
        {{ directory.feedback.value }}
      </p>

      <JobDirectoryTable
        :jobs="directory.jobs.value"
        :load-state="directory.loadState.value"
        :load-error="directory.loadError.value"
        :busy-job-ids="directory.busyJobIds.value"
        :row-errors="directory.rowErrors.value"
        :expanded="directory.expanded.value"
        :awaited-run-ids="directory.awaitedRunIds"
        @refresh="directory.load"
        @toggle-enabled="directory.toggleEnabled"
        @run-now="directory.runNow"
        @remove="directory.removeJob"
        @toggle-edit="directory.togglePanel($event.id, 'edit')"
        @toggle-history="directory.togglePanel($event.id, 'history')"
        @run-finished="directory.handleRunFinished"
      >
        <template #edit="{ job }">
          <JobForm
            mode="edit"
            :job="job"
            :key-value="editForm.key.value"
            :task-type="editForm.taskType.value"
            :cron-expr="editForm.cronExpr.value"
            :limit-per-source="editForm.limitPerSource.value"
            :batch-size="editForm.batchSize.value"
            :stale-after-minutes="editForm.staleAfterMinutes.value"
            :retention-days="editForm.retentionDays.value"
            :dry-run="editForm.dryRun.value"
            :knowledge-base-ids="editForm.knowledgeBaseIds.value"
            :knowledge-base-options="knowledgeBaseOptions"
            :task-types="directory.taskTypes.value"
            :enabled="editForm.enabled.value"
            :errors="editForm.errors.value"
            :form-error="editForm.formError.value"
            :submitting="editForm.submitting.value"
            @update:key-value="editForm.key.value = $event"
            @update:task-type="editForm.setTaskType($event)"
            @update:cron-expr="editForm.cronExpr.value = $event"
            @update:limit-per-source="editForm.limitPerSource.value = $event"
            @update:batch-size="editForm.batchSize.value = $event"
            @update:stale-after-minutes="editForm.staleAfterMinutes.value = $event"
            @update:retention-days="editForm.retentionDays.value = $event"
            @update:dry-run="editForm.dryRun.value = $event"
            @update:knowledge-base-ids="editForm.knowledgeBaseIds.value = $event"
            @update:enabled="editForm.enabled.value = $event"
            @submit="editForm.submit()"
            @close="editForm.close()"
          />
        </template>
      </JobDirectoryTable>
    </div>
  </section>
</template>

<style scoped>
.admin-page {
  padding-top: 8px;
}

.page-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 26px;
  border-bottom: 1px solid var(--border-subtle);
}

.page-intro {
  max-width: 640px;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.6;
}

.page-actions,
.task-views {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  padding: 10px 12px;
  border-left: 3px solid var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
  font-size: var(--fs-xs);
}

.empty-hint {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 14px;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

@container (max-width: 640px) {
  .page-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
}
</style>
