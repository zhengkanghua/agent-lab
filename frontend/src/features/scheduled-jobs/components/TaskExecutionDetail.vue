<script setup lang="ts">
import type { JobRunDto } from '@/api/tasks'
import BaseButton from '@/shared/ui/BaseButton.vue'
import {
  executionStatusLabel,
  formatBeijingTime,
  formatRunStats,
  taskTypeLabel,
  triggerTypeLabel,
} from '../model/job-copy'

defineProps<{ run: JobRunDto; busy: boolean; retryPending: boolean }>()
defineEmits<{ cancel: []; retry: []; 'open-run': [id: string] }>()
const time = (value: string | null) => (value ? formatBeijingTime(value) : '—')
</script>

<template>
  <article class="task-detail" aria-label="任务执行详情">
    <header>
      <div>
        <h2>{{ taskTypeLabel(run.task_type) }}</h2>
        <p class="run-id">{{ run.id }}</p>
      </div>
      <strong
        :class="{
          warning: run.needs_attention || run.status === 'retry_wait',
          failure: run.status === 'failed',
        }"
        >{{ executionStatusLabel(run) }}</strong
      >
    </header>
    <p class="result-summary">{{ formatRunStats(run) }}</p>
    <div class="run-actions">
      <BaseButton
        v-if="run.can_cancel"
        variant="secondary"
        :disabled="busy"
        @click="$emit('cancel')"
        >取消此次执行</BaseButton
      >
      <BaseButton
        v-if="run.can_retry"
        variant="primary"
        :disabled="busy || retryPending"
        @click="$emit('retry')"
        >使用原参数重试</BaseButton
      >
      <BaseButton v-if="run.retry_of" variant="ghost" @click="$emit('open-run', run.retry_of)"
        >查看原失败执行</BaseButton
      >
    </div>
    <p v-if="run.can_retry" class="hint">重试新建执行，沿用失败时参数，采用当前默认策略。</p>
    <section v-if="run.needs_attention" class="verification" aria-label="核实说明">
      <h3>需要核实后继续</h3>
      <p>{{ run.wait_reason || '旧执行者或远端写入状态尚未确认。' }}</p>
      <p>
        {{
          typeof run.recovery.steps === 'string'
            ? run.recovery.steps
            : '请由维护人员确认旧进程和远端请求已停止，再使用维护入口记录核实结论。'
        }}
      </p>
    </section>
    <dl>
      <div>
        <dt>来源</dt>
        <dd>{{ triggerTypeLabel(run.trigger_type) }}</dd>
      </div>
      <div>
        <dt>周期配置</dt>
        <dd>
          {{
            run.source_job_id
              ? run.job_id
                ? run.source_job_id
                : `已删除 · ${run.source_job_id}`
              : '一次性执行'
          }}
        </dd>
      </div>
      <div>
        <dt>受理时间</dt>
        <dd>{{ time(run.accepted_at) }}</dd>
      </div>
      <div>
        <dt>计划时间</dt>
        <dd>{{ time(run.scheduled_for) }}</dd>
      </div>
      <div>
        <dt>开始时间</dt>
        <dd>{{ time(run.started_at) }}</dd>
      </div>
      <div>
        <dt>结束时间</dt>
        <dd>{{ time(run.finished_at) }}</dd>
      </div>
      <div>
        <dt>业务尝试</dt>
        <dd>{{ run.attempts }} 次（最多 {{ run.policy_snapshot.max_retries + 1 }} 次）</dd>
      </div>
      <div v-if="['queued', 'waiting_resource', 'retry_wait'].includes(run.status)">
        <dt>下次可处理时间</dt>
        <dd>{{ time(run.available_at) }}</dd>
      </div>
      <div>
        <dt>投递</dt>
        <dd>{{ run.delivery_count }} 次 · {{ time(run.last_dispatched_at) }}</dd>
      </div>
      <div v-if="run.dispatch_error_type">
        <dt>最近投递错误</dt>
        <dd>{{ run.dispatch_error_type }}</dd>
      </div>
      <div v-if="run.error_type">
        <dt>业务错误</dt>
        <dd>{{ run.error_type }}</dd>
      </div>
      <div v-if="run.heartbeat_at">
        <dt>最近执行心跳</dt>
        <dd>{{ time(run.heartbeat_at) }}</dd>
      </div>
      <div>
        <dt>详情保留至</dt>
        <dd>{{ time(run.expires_at) }}</dd>
      </div>
    </dl>
    <p class="hint">以上具体时刻均显示为北京时间（Asia/Shanghai）。资源等待不消耗业务尝试次数。</p>
    <details>
      <summary>原始结果摘要</summary>
      <pre>{{ JSON.stringify(run.stats, null, 2) }}</pre>
    </details>
    <details>
      <summary>受理时的参数与策略</summary>
      <pre>{{
        JSON.stringify({ configuration: run.config_snapshot, policy: run.policy_snapshot }, null, 2)
      }}</pre>
    </details>
    <details v-if="Object.keys(run.recovery).length">
      <summary>恢复依据</summary>
      <pre>{{ JSON.stringify(run.recovery, null, 2) }}</pre>
    </details>
    <RouterLink
      v-if="run.task_type === 'document_processing' || run.task_type === 'index_pending'"
      to="/admin/documents"
      >查看文档处理与审核状态</RouterLink
    >
  </article>
</template>

<style scoped>
.task-detail {
  border-top: 2px solid var(--border-strong);
  padding-top: var(--space-5);
  margin-top: var(--space-6);
  font-size: var(--fs-sm);
}
header,
.run-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
h2 {
  font-size: var(--fs-lg);
}
h3 {
  font-size: var(--fs-sm);
}
.run-id {
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  margin-top: 4px;
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}
.result-summary,
.run-actions {
  margin-top: var(--space-4);
}
.run-actions {
  justify-content: flex-start;
}
.hint {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  margin-top: var(--space-2);
}
.warning {
  color: var(--warning);
}
.failure {
  color: var(--danger);
}
.verification {
  background: var(--warning-soft);
  padding: var(--space-4);
  margin-top: var(--space-4);
}
.verification p {
  margin-top: var(--space-2);
}
dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-5);
}
dt {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}
details {
  border-top: 1px solid var(--border-subtle);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
}
summary {
  cursor: pointer;
}
pre {
  background: var(--surface-sunken);
  padding: var(--space-3);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin-top: var(--space-3);
  font-size: var(--fs-xs);
}
a {
  display: inline-block;
  margin-top: var(--space-4);
}
@media (max-width: 600px) {
  dl {
    grid-template-columns: 1fr;
  }
}
</style>
