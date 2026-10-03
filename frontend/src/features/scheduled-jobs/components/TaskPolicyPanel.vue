<script setup lang="ts">
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
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
    <!-- 面板级错误一律走 BaseCallout（与其余表单同一形态：图标 + 说明 + 可选操作），
         不再各写一个 .error 段落。 -->
    <BaseCallout
      v-if="policy.loadError.value"
      tone="danger"
      :description="policy.loadError.value"
    />
    <form v-if="policy.draft.value" @submit.prevent="policy.save">
      <!-- 字段外壳与说明的 aria 接线归 BaseField（hint 会被 aria-describedby 指到）。 -->
      <BaseField label="最多自动重试次数" hint="初次尝试之外的次数；0 表示不自动重试。">
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="policy.draft.value.max_retries"
            type="number"
            min="0"
            max="20"
            step="1"
            :disabled="policy.saving.value"
          />
        </template>
      </BaseField>
      <BaseField
        label="首次重试间隔（秒）"
        hint="之后逐次翻倍，最长一天；只有业务确认可安全重试的错误才适用。"
      >
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="policy.draft.value.retry_delay_seconds"
            type="number"
            min="1"
            max="86400"
            step="1"
            :disabled="policy.saving.value"
          />
        </template>
      </BaseField>
      <BaseField
        label="普通历史保留（天）"
        hint="从结束时间计算；未结束、待核实和必要的重试关联受到保护。"
      >
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="policy.draft.value.history_retention_days"
            type="number"
            min="1"
            max="36500"
            step="1"
            :disabled="policy.saving.value"
          />
        </template>
      </BaseField>
      <BaseButton variant="primary" type="submit" :loading="policy.saving.value"
        >保存默认策略</BaseButton
      >
    </form>
    <BaseButton variant="ghost" size="sm" :disabled="policy.saving.value" @click="policy.reload"
      >重新读取</BaseButton
    >
    <BaseCallout v-if="policy.error.value" tone="danger" :description="policy.error.value" />
    <p v-if="policy.feedback.value" role="status">{{ policy.feedback.value }}</p>
    <details>
      <summary>最近修改记录</summary>
      <p v-if="policy.changes.isPending.value" role="status">正在读取修改记录</p>
      <BaseCallout
        v-else-if="policy.changes.isError.value"
        tone="danger"
        description="修改记录读取失败，请重新读取。"
      />
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
/* 字段外壳（标签 + 间距 + aria 接线）归 BaseField，错误面板归 BaseCallout。 */
details {
  margin-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
}
summary {
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out-smooth);
}
summary:hover {
  color: var(--accent);
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
