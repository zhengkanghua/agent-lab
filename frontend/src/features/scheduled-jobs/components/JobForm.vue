<script setup lang="ts">
import { computed } from 'vue'
import { Check, X } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import {
  type ScheduledTaskTypeDto,
  type ScheduledJobDto,
  type ScheduledJobTaskType,
} from '@/api/scheduled-jobs'
import { useCronPreview } from '../composables/useCronPreview'
import { taskTypeLabel } from '../model/job-copy'
import type { JobFormErrors } from '../model/job-validation'
import { parameterBounds, supportsJobForm } from '../model/job-validation'

/*
 * 定时任务的创建/编辑表单（受控组件，字段值由 useJobForm 持有）。
 *
 * cron 预览与提交闸门归本组件：输入停 300ms 调后端 validate-cron，非法或还在校验时
 * 不发出 submit（内联提示说明原因）。字段绑定与关闭按钮的分工照 UserCreateForm。
 */

const props = defineProps<{
  mode: 'create' | 'edit'
  /** 编辑时的任务（用于只读展示 key 与类型）；创建时为 null。 */
  job: ScheduledJobDto | null
  keyValue: string
  taskType: ScheduledJobTaskType
  cronExpr: string
  limitPerSource: number
  batchSize: number
  staleAfterMinutes: number
  retentionDays: number
  dryRun: boolean
  taskTypes: ScheduledTaskTypeDto[]
  enabled: boolean
  errors: JobFormErrors
  formError: string
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:keyValue': [value: string]
  'update:taskType': [value: ScheduledJobTaskType]
  'update:cronExpr': [value: string]
  'update:limitPerSource': [value: number]
  'update:batchSize': [value: number]
  'update:staleAfterMinutes': [value: number]
  'update:retentionDays': [value: number]
  'update:dryRun': [value: boolean]
  'update:enabled': [value: boolean]
  submit: []
  close: []
}>()

const keyDraft = computed({
  get: () => props.keyValue,
  set: (value: string) => emit('update:keyValue', value),
})
const taskTypeDraft = computed({
  get: () => props.taskType,
  set: (value: string) => emit('update:taskType', value as ScheduledJobTaskType),
})
const cronDraft = computed({
  get: () => props.cronExpr,
  set: (value: string) => emit('update:cronExpr', value),
})
const limitDraft = computed({
  get: () => props.limitPerSource,
  set: (value: number) => emit('update:limitPerSource', value),
})
const batchDraft = computed({
  get: () => props.batchSize,
  set: (value: number) => emit('update:batchSize', value),
})
const staleDraft = computed({
  get: () => props.staleAfterMinutes,
  set: (value: number) => emit('update:staleAfterMinutes', value),
})
const enabledDraft = computed({
  get: () => props.enabled,
  set: (value: boolean) => emit('update:enabled', value),
})

const isCreate = computed(() => props.mode === 'create')
const selectedSpec = computed(() =>
  props.taskTypes.find((item) => item.task_type === props.taskType),
)
const syncBounds = computed(() => parameterBounds(selectedSpec.value, 'limit_per_source'))
const batchBounds = computed(() => parameterBounds(selectedSpec.value, 'batch_size'))
const staleBounds = computed(() => parameterBounds(selectedSpec.value, 'stale_after_minutes'))
const retentionBounds = computed(() => parameterBounds(selectedSpec.value, 'retention_days'))
const retentionDraft = computed({
  get: () => props.retentionDays,
  set: (value: number) => emit('update:retentionDays', value),
})
const dryRunDraft = computed({
  get: () => props.dryRun,
  set: (value: boolean) => emit('update:dryRun', value),
})

const {
  state: previewState,
  previewTimes,
  shapeMessage: previewShapeMessage,
  message: previewFailureMessage,
  canSubmit: previewCanSubmit,
  timezone,
} = useCronPreview(cronDraft)

const previewText = computed(() => {
  if (previewState.value === 'checking') return '正在校验 cron…'
  if (previewState.value === 'valid' && previewTimes.value.length > 0) {
    return `接下来 3 次：${previewTimes.value.join('、')}`
  }
  return ''
})

/** cron 没校验通过就不发出提交；具体原因已经在预览区里写明。 */
function onSubmit(): void {
  if (!previewCanSubmit.value || props.submitting || !selectedSpec.value) return
  emit('submit')
}
</script>

<template>
  <section
    class="job-editor"
    aria-labelledby="job-editor-title"
    style="container-type: inline-size"
  >
    <div class="editor-heading">
      <div>
        <p>{{ isCreate ? '新建定时任务' : `编辑定时任务「${job?.key ?? ''}」` }}</p>
        <h2 id="job-editor-title">
          {{ isCreate ? '任务配置' : '执行节奏与参数' }}
        </h2>
      </div>
      <BaseIconButton label="关闭表单" busy-cursor :disabled="submitting" @click="emit('close')">
        <X :size="18" aria-hidden="true" />
      </BaseIconButton>
    </div>

    <form class="job-form" novalidate @submit.prevent="onSubmit">
      <label class="field-control">
        <span>任务类型</span>
        <BaseSelect v-if="isCreate" v-model="taskTypeDraft" :disabled="submitting">
          <option
            v-for="type in taskTypes"
            :key="type.task_type"
            :value="type.task_type"
            :disabled="!supportsJobForm(type.task_type)"
          >
            {{ taskTypeLabel(type.task_type)
            }}{{ supportsJobForm(type.task_type) ? '' : '（暂不支持编辑）' }}
          </option>
        </BaseSelect>
        <BaseInput v-else :model-value="taskTypeLabel(taskType)" disabled />
        <em v-if="errors.taskType" class="field-error">{{ errors.taskType }}</em>
      </label>

      <label v-if="isCreate" class="field-control">
        <span>任务标识</span>
        <BaseInput
          v-model="keyDraft"
          name="job-key"
          autocomplete="off"
          placeholder="freshrss-sync"
          :disabled="submitting"
          :aria-invalid="errors.key !== undefined"
        />
        <small>小写字母、数字与短横线；创建后不可修改。</small>
        <em v-if="errors.key" class="field-error">{{ errors.key }}</em>
      </label>
      <label v-else class="field-control">
        <span>任务标识</span>
        <BaseInput :model-value="job?.key ?? ''" disabled />
      </label>

      <label class="field-control cron-field">
        <span>执行节奏（cron）</span>
        <BaseInput
          v-model="cronDraft"
          name="job-cron"
          autocomplete="off"
          placeholder="*/10 * * * *"
          :disabled="submitting"
          :aria-invalid="errors.cron !== undefined || previewState === 'invalid'"
        />
        <small v-if="timezone">cron 时区：{{ timezone }}；以下时刻显示为北京时间</small>
        <em v-if="errors.cron" class="field-error">{{ errors.cron }}</em>
        <em v-else-if="previewShapeMessage" class="field-error">{{ previewShapeMessage }}</em>
        <em v-else-if="previewFailureMessage" class="field-error">{{ previewFailureMessage }}</em>
        <small v-else-if="previewText" class="cron-preview">{{ previewText }}</small>
      </label>

      <template v-if="taskType === 'freshrss_sync'">
        <label class="field-control">
          <span>每来源单轮上限</span>
          <BaseInput
            v-model="limitDraft"
            type="number"
            :min="syncBounds.min"
            :max="syncBounds.max"
            :disabled="submitting"
            :aria-invalid="errors.limitPerSource !== undefined"
          />
          <small>{{ syncBounds.min }}–{{ syncBounds.max }} 篇</small>
          <em v-if="errors.limitPerSource" class="field-error">{{ errors.limitPerSource }}</em>
        </label>
      </template>

      <template v-if="taskType === 'index_pending'">
        <label class="field-control">
          <span>单轮索引篇数</span>
          <BaseInput
            v-model="batchDraft"
            type="number"
            :min="batchBounds.min"
            :max="batchBounds.max"
            :disabled="submitting"
            :aria-invalid="errors.batchSize !== undefined"
          />
          <small>{{ batchBounds.min }}–{{ batchBounds.max }} 篇</small>
          <em v-if="errors.batchSize" class="field-error">{{ errors.batchSize }}</em>
        </label>
        <label class="field-control">
          <span>卡死回收阈值</span>
          <BaseInput
            v-model="staleDraft"
            type="number"
            :min="staleBounds.min"
            :max="staleBounds.max"
            :disabled="submitting"
            :aria-invalid="errors.staleAfterMinutes !== undefined"
          />
          <small>{{ staleBounds.min }}–{{ staleBounds.max }} 分钟</small>
          <em v-if="errors.staleAfterMinutes" class="field-error">
            {{ errors.staleAfterMinutes }}
          </em>
        </label>
      </template>

      <template v-if="taskType === 'prune_old_documents'">
        <label class="field-control">
          <span>保留天数</span>
          <BaseInput
            v-model="retentionDraft"
            name="retention-days"
            type="number"
            :min="retentionBounds.min"
            :max="retentionBounds.max"
            :disabled="submitting"
          />
          <small>{{ retentionBounds.min }}–{{ retentionBounds.max }} 天</small>
          <em v-if="errors.retentionDays" class="field-error">{{ errors.retentionDays }}</em>
        </label>
        <label class="check-control">
          <input v-model="dryRunDraft" name="dry-run" type="checkbox" :disabled="submitting" />
          <span
            ><strong>仅预演</strong
            ><small>{{
              dryRun ? '预计删除已完成索引的旧新闻' : '将实际删除已完成索引的旧新闻及其索引'
            }}</small></span
          >
        </label>
      </template>

      <label v-if="isCreate" class="check-control">
        <input v-model="enabledDraft" type="checkbox" :disabled="submitting" />
        <span>
          <strong>启用</strong>
          <small>停用后保留配置，但不再按 cron 执行</small>
        </span>
      </label>

      <BaseButton
        class="submit-command"
        variant="primary"
        type="submit"
        :loading="submitting"
        :disabled="!previewCanSubmit || !selectedSpec"
      >
        <template #icon><Check :size="17" aria-hidden="true" /></template>
        {{ submitting ? '正在保存' : isCreate ? '确认创建' : '确认修改' }}
      </BaseButton>
      <BaseCallout v-if="formError" class="editor-error" tone="danger" :description="formError" />
    </form>
  </section>
</template>

<style scoped>
.job-editor {
  padding: 25px 28px 27px;
  border-top: 3px solid var(--accent);
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-base);
}

.editor-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.editor-heading p {
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 760;
}

.editor-heading h2 {
  margin-top: 4px;
  font-size: 1.24rem;
  font-weight: 760;
}

.job-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  align-items: start;
  gap: 18px;
  margin-top: 22px;
}

/* 输入框/下拉皮肤在 BaseInput / BaseSelect（全站一份）；这里只管外壳排版。 */
.field-control {
  display: grid;
  align-content: start;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 700;
}

.field-control small {
  color: var(--text-tertiary);
  font-size: 0.67rem;
  font-weight: 450;
}

.field-control small.cron-preview {
  color: var(--accent);
}

.field-error {
  color: var(--danger);
  font-size: 0.7rem;
  font-style: normal;
}

.check-control {
  display: flex;
  align-items: center;
  min-height: 42px;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 0.76rem;
}

.check-control input {
  width: 17px;
  height: 17px;
  accent-color: var(--accent);
}

.check-control span {
  display: grid;
  gap: 1px;
}

.check-control small {
  color: var(--text-tertiary);
  font-size: 0.67rem;
  font-weight: 450;
}

.submit-command {
  align-self: end;
}

.editor-error {
  grid-column: 1 / -1;
}

@container (max-width: 720px) {
  .job-editor {
    padding: 22px 17px 24px;
  }

  .job-form {
    grid-template-columns: 1fr;
  }
}
</style>
