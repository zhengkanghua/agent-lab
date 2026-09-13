<script setup lang="ts">
import BaseButton from '@/shared/ui/BaseButton.vue'
import type { TaskSubmissions } from '../composables/useTaskSubmissions'
import { formatBeijingTime } from '../model/job-copy'

defineProps<{ submissions: TaskSubmissions }>()
defineEmits<{ 'open-run': [runId: string] }>()
</script>

<template>
  <div class="submission-notice">
    <p v-if="submissions.feedback.value" role="status">{{ submissions.feedback.value }}</p>
    <p v-if="submissions.error.value" class="submission-error" role="alert">
      {{ submissions.error.value }}
      <BaseButton
        v-if="submissions.conflictRunId.value"
        variant="ghost"
        size="sm"
        @click="$emit('open-run', submissions.conflictRunId.value)"
        >查看相关执行</BaseButton
      >
    </p>
    <section v-if="submissions.pending.value.length" aria-label="待确认请求">
      <h2>待确认请求</h2>
      <p>离开页面不会停止任务。以下操作沿用原请求内容确认受理结果。</p>
      <ul>
        <li v-for="item in submissions.pending.value" :key="item.key">
          <div>
            <strong>{{ item.label }}</strong>
            <small>{{ formatBeijingTime(item.createdAt) }}（北京时间）</small>
            <details v-if="item.command.kind === 'pipeline'">
              <summary>原请求参数</summary>
              <pre>{{ JSON.stringify(item.command.params, null, 2) }}</pre>
            </details>
          </div>
          <BaseButton
            variant="secondary"
            size="sm"
            :disabled="!!submissions.busyKey.value"
            @click="submissions.resend(item)"
            >{{ submissions.busyKey.value === item.key ? '正在确认' : '确认受理' }}</BaseButton
          >
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.submission-notice {
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}
.submission-notice > p {
  margin-top: var(--space-4);
}
.submission-error {
  color: var(--danger);
}
section {
  margin-top: var(--space-5);
  padding: var(--space-4);
  border-left: 3px solid var(--warning);
  background: var(--warning-soft);
}
h2 {
  font-size: var(--fs-base);
}
section p,
small {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
ul {
  padding: 0;
  margin-top: var(--space-3);
  list-style: none;
}
li {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) 0;
}
small {
  display: block;
  margin-top: 4px;
}
pre {
  white-space: pre-wrap;
}
</style>
