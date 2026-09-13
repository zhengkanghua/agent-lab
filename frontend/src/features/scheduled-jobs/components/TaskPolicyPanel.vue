<script setup lang="ts">
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import { useTaskPolicy } from '../composables/useTaskPolicy'
import { formatBeijingTime } from '../model/job-copy'

const policy = useTaskPolicy()
defineEmits<{ close: [] }>()
</script>

<template>
  <section class="policy-panel" aria-label="任务默认策略">
    <header>
      <h2>任务默认策略</h2>
      <BaseButton variant="ghost" size="sm" :disabled="policy.saving.value" @click="$emit('close')"
        >关闭</BaseButton
      >
    </header>
    <p class="hint">每次受理会保存当时的策略。修改不影响已有执行和它的自动重试。</p>
    <p v-if="policy.query.isPending.value" role="status">正在读取策略</p>
    <p v-if="policy.loadError.value" class="error" role="alert">{{ policy.loadError.value }}</p>
    <form v-if="policy.draft.value" @submit.prevent="policy.save">
      <label
        >最多自动重试次数<BaseInput
          v-model="policy.draft.value.max_retries"
          type="number"
          min="0"
          max="20"
          step="1"
          :disabled="policy.saving.value"
        /><small>初次尝试之外的次数；0 表示不自动重试。</small></label
      >
      <label
        >首次重试间隔（秒）<BaseInput
          v-model="policy.draft.value.retry_delay_seconds"
          type="number"
          min="1"
          max="86400"
          step="1"
          :disabled="policy.saving.value"
        /><small>之后逐次翻倍，最长一天；只有业务确认可安全重试的错误才适用。</small></label
      >
      <label
        >普通历史保留（天）<BaseInput
          v-model="policy.draft.value.history_retention_days"
          type="number"
          min="1"
          max="36500"
          step="1"
          :disabled="policy.saving.value"
        /><small>从结束时间计算；未结束、待核实和必要的重试关联受到保护。</small></label
      >
      <BaseButton variant="primary" type="submit" :loading="policy.saving.value"
        >保存默认策略</BaseButton
      >
    </form>
    <BaseButton variant="ghost" size="sm" :disabled="policy.saving.value" @click="policy.reload"
      >重新读取</BaseButton
    >
    <p v-if="policy.error.value" class="error" role="alert">{{ policy.error.value }}</p>
    <p v-if="policy.feedback.value" role="status">{{ policy.feedback.value }}</p>
    <details>
      <summary>最近修改记录</summary>
      <p v-if="policy.changes.isPending.value" role="status">正在读取修改记录</p>
      <p v-else-if="policy.changes.isError.value" class="error" role="alert">
        修改记录读取失败，请重新读取。
      </p>
      <p v-else-if="!policy.changes.data.value?.length" class="hint">尚无修改记录。</p>
      <ol v-else>
        <li v-for="item in policy.changes.data.value" :key="item.id">
          <p>{{ formatBeijingTime(item.changed_at) }}（北京时间）</p>
          <small>{{ item.actor }}</small>
          <p>
            自动重试 {{ item.previous.max_retries }} → {{ item.current.max_retries }} 次；首次间隔
            {{ item.previous.retry_delay_seconds }} →
            {{ item.current.retry_delay_seconds }} 秒；历史
            {{ item.previous.history_retention_days }} →
            {{ item.current.history_retention_days }} 天。
          </p>
        </li>
      </ol>
    </details>
  </section>
</template>

<style scoped>
.policy-panel {
  padding: var(--space-5);
  overflow-y: auto;
  font-size: var(--fs-sm);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
h2 {
  font-size: var(--fs-lg);
}
.hint,
small {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.hint {
  margin-top: var(--space-2);
}
form {
  display: grid;
  gap: var(--space-4);
  margin: var(--space-5) 0;
}
label {
  display: grid;
  gap: 7px;
}
.error {
  color: var(--danger);
  margin-top: var(--space-3);
}
details {
  margin-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
}
summary {
  cursor: pointer;
}
ol {
  padding-left: var(--space-5);
}
li {
  padding: var(--space-3) 0;
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}
</style>
