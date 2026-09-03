<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { CalendarClock, Check, Plus } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import {
  JobDirectoryTable,
  JobForm,
  useJobForm,
  useScheduledJobDirectory,
} from '@/features/scheduled-jobs'

/* 定时任务管理页：作为 /admin 的子路由渲染在 AdminShell 的内容区里。
 * 侧边栏、顶部标题栏、退出登录都由 AdminShell 提供；本页只负责正文内容。
 * 创建与编辑共用 JobForm（受控字段归 useJobForm），行内操作归 useScheduledJobDirectory。 */

const directory = useScheduledJobDirectory()

const createPanel = ref(false)

const createForm = useJobForm({
  mode: 'create',
  job: null,
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
})

function openCreate(): void {
  createPanel.value = true
  directory.clearFeedback()
}
</script>

<template>
  <section class="admin-page" aria-labelledby="admin-title" style="container-type: inline-size">
    <div class="page-bar">
      <p class="page-intro">
        配置定时任务，让 FreshRSS 同步与向量索引按 cron
        自动执行；到点自动跑，也能在这里立即执行或回看历史。
      </p>
      <BaseButton v-if="!createPanel" variant="primary" @click="openCreate">
        <template #icon><Plus :size="18" aria-hidden="true" /></template>
        新建任务
      </BaseButton>
    </div>

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
      :edit-form="editForm"
      :awaited-run-ids="directory.awaitedRunIds"
      @refresh="directory.load"
      @toggle-enabled="directory.toggleEnabled"
      @run-now="directory.runNow"
      @remove="directory.removeJob"
      @toggle-edit="directory.togglePanel($event.id, 'edit')"
      @toggle-history="directory.togglePanel($event.id, 'history')"
      @submit-edit="editForm.submit()"
      @run-finished="directory.handleRunFinished"
      @update:key-value="editForm.key.value = $event"
      @update:task-type="editForm.setTaskType($event)"
      @update:cron-expr="editForm.cronExpr.value = $event"
      @update:limit-per-source="editForm.limitPerSource.value = $event"
      @update:batch-size="editForm.batchSize.value = $event"
      @update:stale-after-minutes="editForm.staleAfterMinutes.value = $event"
      @update:enabled="editForm.enabled.value = $event"
    />

    <p v-if="directory.jobs.value.length === 0" class="empty-hint" aria-hidden="true">
      <CalendarClock :size="14" aria-hidden="true" />
      提示：调度器开关在服务端 .env（SCHEDULER_ENABLED），任务启停在这里控制。
    </p>
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
  font-size: 0.9rem;
  line-height: 1.6;
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
  font-size: 0.77rem;
}

.empty-hint {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 14px;
  color: var(--text-tertiary);
  font-size: 0.72rem;
}

@container (max-width: 640px) {
  .page-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
}
</style>
