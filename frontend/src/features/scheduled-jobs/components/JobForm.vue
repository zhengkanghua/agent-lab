<script setup lang="ts">
import { computed } from 'vue'
import { Check, X } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import type { KnowledgeBaseDto } from '@/api/knowledge-bases'
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
  /** 清理范围多选的当前值，启用和停用库都可维护清理。 */
  knowledgeBaseIds: string[]
  knowledgeBaseOptions: KnowledgeBaseDto[]
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
  'update:knowledgeBaseIds': [value: string[]]
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
const commonSchedules = [
  { label: '每 10 分钟', value: '*/10 * * * *' },
  { label: '每小时整点', value: '0 * * * *' },
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '每天 00:30', value: '30 0 * * *' },
  { label: '每周一 09:00', value: '0 9 * * mon' },
]
const commonSchedule = computed({
  get: () => (commonSchedules.some((item) => item.value === props.cronExpr) ? props.cronExpr : ''),
  set: (value: string) => {
    if (value) emit('update:cronExpr', value)
  },
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

const unresolvedScopeIds = computed(() =>
  props.knowledgeBaseIds.filter(
    (id) => !props.knowledgeBaseOptions.some((option) => option.id === id),
  ),
)
const scopeOptions = computed(() => [
  ...props.knowledgeBaseOptions,
  ...unresolvedScopeIds.value.map((id) => ({
    id,
    name: `未找到的知识库 ${id}`,
    key: id,
    is_active: null,
  })),
])

/** 单个知识库的勾选切换；值数组保持选项顺序，提交形状与后端一致。 */
function toggleKnowledgeBase(id: string, checked: boolean): void {
  const next = checked
    ? [...props.knowledgeBaseIds, id]
    : props.knowledgeBaseIds.filter((item) => item !== id)
  emit('update:knowledgeBaseIds', next)
}

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
  if (props.taskType === 'prune_old_documents' && unresolvedScopeIds.value.length > 0) return
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
      <!-- 字段外壳走 BaseField：标签、aria-invalid 与 aria-describedby 由它算一次，
           不再每个字段手写一遍（此前这 12 个字段各写各的，接线漏一处也不报错）。 -->
      <BaseField label="任务类型" :error="errors.taskType">
        <template #default="{ control }">
          <BaseSelect
            v-if="isCreate"
            v-bind="control"
            v-model="taskTypeDraft"
            :disabled="submitting"
          >
            <option
              v-for="type in taskTypes.filter((item) => item.schedulable)"
              :key="type.task_type"
              :value="type.task_type"
              :disabled="!supportsJobForm(type.task_type)"
            >
              {{ taskTypeLabel(type.task_type)
              }}{{ supportsJobForm(type.task_type) ? '' : '（暂不支持编辑）' }}
            </option>
          </BaseSelect>
          <BaseInput v-else v-bind="control" :model-value="taskTypeLabel(taskType)" disabled />
        </template>
      </BaseField>

      <BaseField
        v-if="isCreate"
        id="job-key"
        label="任务标识"
        :error="errors.key"
        hint="小写字母、数字与短横线；创建后不可修改。"
      >
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="keyDraft"
            name="job-key"
            autocomplete="off"
            placeholder="freshrss-sync"
            :disabled="submitting"
          />
        </template>
      </BaseField>
      <BaseField v-else label="任务标识">
        <template #default="{ control }">
          <BaseInput v-bind="control" :model-value="job?.key ?? ''" disabled />
        </template>
      </BaseField>

      <BaseField label="常用周期" hint="按下方调度时区解释，也可直接修改 cron。">
        <template #default="{ control }">
          <BaseSelect v-bind="control" v-model="commonSchedule" :disabled="submitting">
            <option value="">自定义 cron</option>
            <option v-for="item in commonSchedules" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </BaseSelect>
        </template>
      </BaseField>

      <!-- 校验失败的原因与「未来的执行时刻」是互斥的两条说明：BaseField 的 error
           与 hint 正好是这层关系（有 error 就不显示 hint），分支链因此压成两个表达式。
           代价：cron 非法时那行「cron 时区」也不显示——错误态只留错误，避免冲淡。 -->
      <BaseField
        class="cron-field"
        label="执行节奏（cron）"
        :error="errors.cron ?? previewShapeMessage ?? previewFailureMessage ?? undefined"
      >
        <template #default="{ control }">
          <BaseInput
            v-bind="control"
            v-model="cronDraft"
            name="job-cron"
            autocomplete="off"
            placeholder="*/10 * * * *"
            :disabled="submitting"
          />
        </template>
        <template #hint>
          <span v-if="timezone" class="cron-timezone">
            cron 时区：{{ timezone }}；以下时刻显示为北京时间
          </span>
          <span v-if="previewText" class="cron-preview">{{ previewText }}</span>
        </template>
      </BaseField>

      <template v-if="taskType === 'freshrss_sync'">
        <BaseField
          label="每来源单轮上限"
          :error="errors.limitPerSource"
          :hint="`${syncBounds.min}–${syncBounds.max} 篇`"
        >
          <template #default="{ control }">
            <BaseInput
              v-bind="control"
              v-model="limitDraft"
              type="number"
              :min="syncBounds.min"
              :max="syncBounds.max"
              :disabled="submitting"
            />
          </template>
        </BaseField>
      </template>

      <template v-if="taskType === 'index_pending'">
        <BaseField
          label="单轮索引篇数"
          :error="errors.batchSize"
          :hint="`${batchBounds.min}–${batchBounds.max} 篇`"
        >
          <template #default="{ control }">
            <BaseInput
              v-bind="control"
              v-model="batchDraft"
              type="number"
              :min="batchBounds.min"
              :max="batchBounds.max"
              :disabled="submitting"
            />
          </template>
        </BaseField>
        <BaseField
          label="卡死回收阈值"
          :error="errors.staleAfterMinutes"
          :hint="`${staleBounds.min}–${staleBounds.max} 分钟`"
        >
          <template #default="{ control }">
            <BaseInput
              v-bind="control"
              v-model="staleDraft"
              type="number"
              :min="staleBounds.min"
              :max="staleBounds.max"
              :disabled="submitting"
            />
          </template>
        </BaseField>
      </template>

      <template v-if="taskType === 'prune_old_documents'">
        <BaseField
          label="保留天数"
          :error="errors.retentionDays"
          :hint="`${retentionBounds.min}–${retentionBounds.max} 天`"
        >
          <template #default="{ control }">
            <BaseInput
              v-bind="control"
              v-model="retentionDraft"
              name="retention-days"
              type="number"
              :min="retentionBounds.min"
              :max="retentionBounds.max"
              :disabled="submitting"
            />
          </template>
        </BaseField>
        <fieldset class="scope-field">
          <legend>清理范围</legend>
          <div class="scope-options">
            <label v-for="option in scopeOptions" :key="option.id" class="check-control">
              <input
                type="checkbox"
                :value="option.id"
                :checked="knowledgeBaseIds.includes(option.id)"
                :disabled="submitting"
                :aria-label="`清理知识库 ${option.name}`"
                @change="
                  toggleKnowledgeBase(option.id, ($event.target as HTMLInputElement).checked)
                "
              />
              <span>
                <strong
                  >{{ option.name }}{{ option.is_active === false ? '（已停用）' : '' }}</strong
                >
                <small>{{ option.key }}</small>
              </span>
            </label>
          </div>
          <em v-if="unresolvedScopeIds.length" class="scope-error"
            >部分知识库未找到，清理范围需要更新。</em
          >
          <small>未选中的库不会被本任务清理；列表中的库共用同一保留周期。</small>
          <em v-if="errors.knowledgeBaseIds" class="scope-error">
            {{ errors.knowledgeBaseIds }}
          </em>
        </fieldset>
        <label class="check-control">
          <input v-model="dryRunDraft" name="dry-run" type="checkbox" :disabled="submitting" />
          <span
            ><strong>仅预演</strong
            ><small>{{
              dryRun ? '预计删除已完成索引的旧文档' : '将实际删除已完成索引的旧文档及其索引'
            }}</small></span
          >
        </label>
      </template>

      <label class="check-control">
        <input v-model="enabledDraft" type="checkbox" :disabled="submitting" />
        <span>
          <strong>启用</strong>
          <small>停用只停止未来周期，已受理执行继续处理</small>
        </span>
      </label>

      <p v-if="!isCreate" class="form-note">修改只用于之后受理的执行；已有执行继续使用原参数。</p>

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
/* 挂进 BaseDialog 后容器感（底色、描边、圆角）归对话框面板，这里只留内边距与滚动。 */
.job-editor {
  padding: var(--space-5);
  overflow-y: auto;
}

.editor-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.editor-heading p {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
}

.editor-heading h2 {
  margin-top: 4px;
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
}

.job-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  align-items: start;
  gap: 18px;
  margin-top: 22px;
}

/* 字段外壳（标签 / 说明 / 错误 / aria 接线）归 BaseField，输入框与下拉皮肤归
   BaseInput / BaseSelect，复选行外观归 styles/components/form-controls.css。
   这里只剩三类本表单自己的排版：不经过 BaseField 的 fieldset、说明段落、以及
   cron 预览那两行说明字的颜色。 */

.form-note {
  grid-column: 1 / -1;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

/* 说明插槽里的两行文字：时区是一般说明，未来执行时刻用强调色。
   两行各自成块——它们是两条独立信息（时区、接下来的执行时刻），
   并排成一行读起来会连成一句。 */
.cron-timezone {
  display: block;
  color: var(--text-tertiary);
}

.cron-preview {
  display: block;
  color: var(--accent);
}

.scope-error {
  color: var(--danger);
  font-size: var(--fs-xs);
  font-style: normal;
}

/* fieldset 不走 BaseField（它需要 legend 而不是 label，且内部是一组复选行），
   所以这里补回原来从 .field-control 借来的网格与说明字排版。 */
.scope-field {
  display: grid;
  align-content: start;
  gap: 7px;
  border: 0;
  padding: 0;
  margin: 0;
}

.scope-field legend {
  padding: 0;
  margin-bottom: 8px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
}

.scope-field small {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-normal);
}

.scope-options {
  display: grid;
  gap: 8px;
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
