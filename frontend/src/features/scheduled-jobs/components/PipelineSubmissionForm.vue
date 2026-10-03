<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import type { ScheduledTaskTypeDto } from '@/api/scheduled-jobs'
import type { PipelineRequest } from '@/api/tasks'
import { parameterBounds } from '../model/job-validation'

const props = defineProps<{
  spec: ScheduledTaskTypeDto | undefined
  busy: boolean
  pending: boolean
}>()
const emit = defineEmits<{ submit: [params: PipelineRequest]; close: [] }>()
const params = reactive<PipelineRequest>({
  limit_per_source: 0,
  batch_size: 0,
  stale_after_minutes: 0,
})
const fields = [
  { key: 'limit_per_source', label: '每来源同步上限' },
  { key: 'batch_size', label: '本次处理文档上限' },
  { key: 'stale_after_minutes', label: '超时回收阈值（分钟）' },
] as const
watch(
  () => props.spec,
  (spec) => {
    if (spec) for (const { key } of fields) params[key] = Number(spec.defaults[key])
  },
  { immediate: true },
)
const valid = computed(
  () =>
    !!props.spec &&
    fields.every(({ key }) => {
      const bounds = parameterBounds(props.spec, key)
      return (
        Number.isInteger(params[key]) &&
        (bounds.min === undefined || params[key] >= bounds.min) &&
        (bounds.max === undefined || params[key] <= bounds.max)
      )
    }),
)

/* 取值范围写在字段上。越界时提交键会被禁用，而禁用的原因只有这个范围本身能说清；
   原来三个数值字段只挂了 min/max 属性——那是给浏览器校验和微调箭头用的，页面上看不见。
   写法与 JobForm 的字段提示一致（`min–max`）。 */
function boundsHint(key: (typeof fields)[number]['key']): string | undefined {
  if (!props.spec) return undefined
  const { min, max } = parameterBounds(props.spec, key)
  if (min === undefined || max === undefined) return undefined
  return `${min}–${max}`
}
function submit(): void {
  if (valid.value && !props.busy && !props.pending) emit('submit', { ...params })
}
</script>

<template>
  <section class="pipeline-form">
    <header>
      <h2>手动 Pipeline</h2>
      <BaseButton variant="ghost" size="sm" @click="$emit('close')">关闭</BaseButton>
    </header>
    <p>先同步 FreshRSS，再处理一批文档。受理后返回执行编号，统计在任务详情中更新。</p>
    <form @submit.prevent="submit">
      <!-- 字段外壳走 BaseField：标签与 aria 接线由它算一次。 -->
      <BaseField
        v-for="field in fields"
        :key="field.key"
        :label="field.label"
        :hint="boundsHint(field.key)"
      >
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="params[field.key]"
            type="number"
            step="1"
            :disabled="busy || pending"
            :min="parameterBounds(spec, field.key).min"
            :max="parameterBounds(spec, field.key).max"
          />
        </template>
      </BaseField>
      <p v-if="!spec" role="alert">任务类型尚未加载，请关闭后刷新任务目录。</p>
      <p v-if="pending" role="status">已有待确认请求，请关闭此表单后确认原请求的受理结果。</p>
      <BaseButton variant="primary" type="submit" :disabled="!valid || pending" :loading="busy"
        >提交后台执行</BaseButton
      >
    </form>
  </section>
</template>

<style scoped>
.pipeline-form {
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
p {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  margin-top: var(--space-3);
}
form {
  display: grid;
  gap: var(--space-4);
  margin-top: var(--space-5);
}
/* 字段外壳（标签 + 间距 + aria 接线）归 BaseField。 */
</style>
