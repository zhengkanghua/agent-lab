<script setup lang="ts">
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { getDocumentVersion } from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import DocumentOriginal from './DocumentOriginal.vue'
import DocumentPreview from './DocumentPreview.vue'
import DocumentReviewHistory from './DocumentReviewHistory.vue'
import { useDocumentReview } from './useDocumentReview'
import { processingLabel, usageLabel } from '@/shared/model/document-processing'
import { issueLabel } from './presentation'

const props = defineProps<{ documentId: string }>()
const emit = defineEmits<{ close: []; removed: [] }>()
const review = useDocumentReview(() => props.documentId)
const {
  detail,
  title,
  text,
  loading,
  busy,
  error,
  feedback,
  conflict,
  stale,
  dirty,
  frozen,
  editable,
  canAdopt,
  historyRevision,
} = review
const section = ref('review')
const mobilePane = ref('editor')
const comparison = ref('candidate')
const confirmation = ref<'adopt' | 'reject' | 'delete' | 'source' | null>(null)
let confirmationTarget = ''
const conclusion = ref('')
const confirmPanel = ref<HTMLElement>()
const heading = ref<HTMLElement>()
const isDraft = computed(
  () => detail.value?.document.draft_processing_id === detail.value?.candidate.processing_id,
)
const unavailable = computed(
  () => busy.value || stale.value || conflict.value || !!detail.value?.document.deletion_pending,
)
const filename = computed(
  () =>
    detail.value?.document.upload_filename ||
    props.documentId + (detail.value?.document.source_kind === 'freshrss' ? '.html' : '.txt'),
)
const issues = computed(() => [
  ...new Set([
    ...(detail.value?.candidate.issue_codes ?? []),
    ...(detail.value?.candidate.error_code ? [detail.value.candidate.error_code] : []),
  ]),
])
const adopted = useQuery({
  queryKey: computed(() => [
    'document-version',
    props.documentId,
    detail.value?.document.current_version_id,
  ]),
  queryFn: ({ signal }) =>
    getDocumentVersion(props.documentId, detail.value!.document.current_version_id!, signal),
  enabled: computed(
    () => comparison.value === 'adopted' && !!detail.value?.document.current_version_id,
  ),
  retry: false,
})
const confirmationCopy = computed(
  () =>
    ({
      adopt: '确认采用当前预览？后台会先建立新索引，准备成功后更新正式版本，失败时保留旧版本。',
      reject:
        '确认停止整篇文档使用？后续检索、Agent 和普通全文都不能再使用它。原件与历史保留，之后仍可修正。',
      delete:
        '确认完整删除这篇文档？原件、人工草稿、所有已采用版本、审核结论和索引都将清除，无法恢复。',
      source: '确认换用最新来源？当前人工草稿及未保存编辑将被替换，已采用版本继续保留。',
    })[confirmation.value ?? 'adopt'],
)

async function confirm() {
  let completed = false
  if (confirmation.value === 'adopt') completed = await review.adopt(conclusion.value)
  else if (confirmation.value === 'reject') completed = await review.reject(conclusion.value)
  else if (confirmation.value === 'source') completed = await review.useLatest()
  else if (confirmation.value === 'delete') {
    completed = await review.remove()
    if (completed) emit('removed')
  }
  if (completed) {
    confirmation.value = null
    conclusion.value = ''
  }
}

async function selectCandidate(id: string) {
  await review.selectCandidate(id)
  section.value = 'review'
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onScopeDispose(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => review.confirmLeave())
onBeforeRouteUpdate((to, from) => to.fullPath === from.fullPath || review.confirmLeave())

watch(confirmation, async (value) => {
  if (value) {
    confirmationTarget = targetIdentity()
    await nextTick()
    confirmPanel.value?.focus()
  }
})
function targetIdentity() {
  return [
    detail.value?.document.management_revision,
    detail.value?.candidate.processing_id,
    detail.value?.candidate.candidate_revision,
    detail.value?.candidate.preview_fingerprint,
  ].join(':')
}
let focusedDocument: string | undefined
watch(detail, async (value) => {
  if (confirmation.value && confirmationTarget !== targetIdentity()) {
    confirmation.value = null
    if (!busy.value) feedback.value = '文档或候选已更新，请检查当前结果后重新确认。'
  }
  if (value && value.document.document_id !== focusedDocument) {
    focusedDocument = value.document.document_id
    await nextTick()
    heading.value?.focus()
  }
})
</script>

<template>
  <section class="review-workbench" aria-label="文档审核工作台">
    <div class="review-toolbar">
      <BaseButton variant="ghost" @click="emit('close')">返回文档列表</BaseButton>
      <BaseButton variant="outline" :disabled="busy || loading" @click="review.refresh()"
        >刷新状态</BaseButton
      >
    </div>
    <BaseCallout v-if="error" tone="danger" :description="error" />
    <p v-if="loading && !detail" role="status">正在读取文档及处理结果…</p>
    <template v-if="detail">
      <header class="review-heading">
        <p>
          {{ detail.document.knowledge_base_name }} ·
          {{ detail.document.source_kind === 'file' ? '上传文件' : 'FreshRSS' }}
        </p>
        <h2 ref="heading" tabindex="-1">{{ detail.document.title || '未命名资料' }}</h2>
        <span class="usage-label" :data-usage="detail.document.usage_status">{{
          usageLabel(
            detail.document.usage_status,
            !!detail.document.current_version_id,
            detail.document.knowledge_base_active,
          )
        }}</span>
      </header>
      <div class="version-strip" aria-label="正文与来源关系">
        <div>
          <span>已采用版本</span
          ><strong>{{
            detail.document.current_version_id ? '第 ' + detail.document.revision + ' 版' : '尚无'
          }}</strong>
        </div>
        <div>
          <span>人工草稿</span
          ><strong>{{ detail.draft ? processingLabel(detail.draft.state) : '尚无' }}</strong>
          <button
            v-if="detail.draft && !isDraft"
            type="button"
            :disabled="busy"
            @click="selectCandidate(detail.draft.processing_id)"
          >
            查看草稿
          </button>
        </div>
        <div>
          <span>最新来源</span
          ><strong>{{
            detail.latest_source ? processingLabel(detail.latest_source.state) : '尚无'
          }}</strong>
          <button
            v-if="
              detail.latest_source &&
              detail.latest_source.processing_id !== detail.candidate.processing_id
            "
            type="button"
            :disabled="busy"
            @click="selectCandidate(detail.latest_source.processing_id)"
          >
            查看来源
          </button>
        </div>
      </div>
      <p v-if="detail.draft && detail.latest_source" class="review-note">
        人工草稿与来源内容分别保留。来源同步不会覆盖草稿，换用来源需要明确确认。
      </p>
      <p v-if="feedback" class="review-feedback" role="status">{{ feedback }}</p>
      <BaseCallout
        v-if="conflict"
        tone="danger"
        title="资料已被其他操作更新"
        description="本地编辑已保留。对照服务端最新状态后，选择如何继续；系统不会覆盖较新的草稿。"
      >
        <template #actions>
          <BaseButton :disabled="busy || stale" @click="review.keepLocalEdits()"
            >保留本地编辑并继续</BaseButton
          >
          <BaseButton variant="outline" :disabled="busy" @click="review.discardLocalEdits()"
            >使用服务端内容</BaseButton
          >
        </template>
      </BaseCallout>
      <BaseCallout
        v-if="frozen"
        tone="neutral"
        description="候选已冻结，后台正在准备新索引。旧已采用版本继续可用，完成前不能编辑这份候选。"
      />
      <BaseCallout v-if="issues.length" tone="neutral" title="需要处理">
        {{ issues.map(issueLabel).join('；') }}。可以核对原件、修正正文或重试失败阶段。
      </BaseCallout>
      <div class="review-sections" role="group" aria-label="审核工作区">
        <BaseButton
          :variant="section === 'review' ? 'outline' : 'ghost'"
          :aria-pressed="section === 'review'"
          @click="section = 'review'"
          >正文与预览</BaseButton
        >
        <BaseButton
          :variant="section === 'history' ? 'outline' : 'ghost'"
          :aria-pressed="section === 'history'"
          @click="section = 'history'"
          >版本与审核结论</BaseButton
        >
      </div>
      <DocumentReviewHistory
        v-if="section === 'history'"
        :document-id="documentId"
        :revision="historyRevision + detail.document.management_revision"
        :filename="filename"
        @select="selectCandidate"
      />
      <template v-else>
        <div class="candidate-heading">
          <h3>
            {{ isDraft ? '人工草稿' : '处理结果' }}
            <span>{{ processingLabel(detail.candidate.state) }}</span>
          </h3>
          <div class="review-actions">
            <BaseButton
              v-if="!isDraft"
              variant="outline"
              :disabled="
                unavailable ||
                frozen ||
                !detail.candidate.source_stored ||
                !detail.document.knowledge_base_active
              "
              @click="review.start()"
              >开始人工复核</BaseButton
            >
            <BaseButton
              v-if="detail.latest_source?.source_stored"
              variant="ghost"
              :disabled="unavailable || frozen || !detail.document.knowledge_base_active"
              @click="confirmation = 'source'"
              >换用最新来源</BaseButton
            >
            <BaseButton
              v-if="
                ['failed', 'receiving_failed', 'received', 'adoption_failed'].includes(
                  detail.candidate.state,
                )
              "
              variant="outline"
              :disabled="unavailable || !detail.document.knowledge_base_active"
              @click="review.retry()"
              >重试失败阶段</BaseButton
            >
          </div>
        </div>
        <div class="mobile-pane-switch" role="group" aria-label="手机审核视图">
          <BaseButton
            :variant="mobilePane === 'editor' ? 'outline' : 'ghost'"
            :aria-pressed="mobilePane === 'editor'"
            @click="mobilePane = 'editor'"
            >编辑正文</BaseButton
          >
          <BaseButton
            :variant="mobilePane === 'preview' ? 'outline' : 'ghost'"
            :aria-pressed="mobilePane === 'preview'"
            @click="mobilePane = 'preview'"
            >对照预览</BaseButton
          >
        </div>
        <div class="review-columns">
          <form
            class="editor-pane"
            :data-mobile-visible="mobilePane === 'editor'"
            @submit.prevent="review.save()"
            @keydown.ctrl.s.prevent="review.save()"
            @keydown.meta.s.prevent="review.save()"
          >
            <label>文档标题<input v-model="title" :readonly="!editable" /></label>
            <label class="body-field"
              >{{ detail.candidate.text_format === 'plain' ? '纯文本正文' : 'Markdown 正文' }}
              <textarea
                v-model="text"
                :readonly="!editable"
                spellcheck="false"
                aria-describedby="draft-help"
              />
            </label>
            <p id="draft-help" class="review-note">
              {{
                !isDraft
                  ? '开始人工复核后可以编辑。'
                  : dirty
                    ? '本地修改尚未保存，当前预览不能用于采用。'
                    : '草稿已与服务端同步。保存草稿不会改变正式版本。'
              }}
            </p>
            <div v-if="isDraft" class="review-actions">
              <BaseButton
                type="submit"
                :disabled="!editable || !dirty || !title.trim()"
                :loading="busy"
                >保存草稿</BaseButton
              >
              <BaseButton
                variant="outline"
                :disabled="!editable || !title.trim()"
                @click="review.preview()"
                >保存并重新生成预览</BaseButton
              >
            </div>
          </form>
          <aside
            class="comparison-pane"
            :data-mobile-visible="mobilePane === 'preview'"
            aria-label="正文对照"
          >
            <label class="comparison-select"
              >对照内容
              <select v-model="comparison">
                <option value="candidate">当前候选预览</option>
                <option value="original">原始资料</option>
                <option value="adopted">已采用版本</option>
              </select>
            </label>
            <DocumentOriginal
              v-if="comparison === 'original'"
              :processing-id="detail.candidate.processing_id"
              :filename="filename"
              :stored="detail.candidate.source_stored"
            />
            <template v-else-if="comparison === 'adopted'">
              <p v-if="!detail.document.current_version_id" class="review-note">
                还没有已采用版本。
              </p>
              <p v-else-if="adopted.isPending.value" role="status">正在读取已采用版本…</p>
              <BaseCallout
                v-else-if="adopted.isError.value"
                tone="danger"
                description="已采用版本读取失败。"
              >
                <template #actions
                  ><BaseButton @click="adopted.refetch()">重试读取</BaseButton></template
                >
              </BaseCallout>
              <DocumentPreview
                v-else-if="adopted.data.value"
                :preview="adopted.data.value.preview"
              />
            </template>
            <DocumentPreview
              v-else-if="detail.candidate.preview"
              :preview="detail.candidate.preview"
              :stale="dirty || conflict"
            />
            <BaseCallout
              v-else
              tone="neutral"
              description="尚无有效预览。保存草稿后生成预览，检查正文、目录与 Chunk，再确认采用。"
            />
          </aside>
        </div>
      </template>
      <div class="decision-bar">
        <p class="review-note">预览不会生效，采用成功后才更新检索。</p>
        <div class="review-actions">
          <BaseButton variant="primary" :disabled="!canAdopt" @click="confirmation = 'adopt'"
            >确认采用预览</BaseButton
          >
          <BaseButton
            variant="danger"
            :disabled="unavailable || dirty || detail.document.usage_status === 'rejected'"
            @click="confirmation = 'reject'"
            >拒绝并停止使用</BaseButton
          >
          <BaseButton
            variant="ghost"
            :disabled="busy || stale || conflict"
            @click="confirmation = 'delete'"
            >{{ detail.document.deletion_pending ? '继续完整删除' : '完整删除' }}</BaseButton
          >
        </div>
      </div>
      <div v-if="confirmation" ref="confirmPanel" tabindex="-1" class="decision-confirmation">
        <BaseCallout
          :tone="confirmation === 'delete' || confirmation === 'reject' ? 'danger' : 'info'"
          :description="confirmationCopy"
        />
        <label v-if="confirmation === 'adopt' || confirmation === 'reject'"
          >审核结论（选填）<textarea v-model="conclusion" rows="2" :disabled="busy" />
        </label>
        <div class="review-actions">
          <BaseButton
            :variant="confirmation === 'delete' || confirmation === 'reject' ? 'danger' : 'primary'"
            :loading="busy"
            @click="confirm"
          >
            {{
              {
                adopt: '采用这份预览',
                reject: '确认停止使用',
                delete: '确认完整删除',
                source: '确认替换草稿',
              }[confirmation]
            }}
          </BaseButton>
          <BaseButton variant="outline" :disabled="busy" @click="confirmation = null"
            >取消</BaseButton
          >
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.review-workbench {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.review-toolbar,
.candidate-heading,
.decision-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.review-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  justify-items: start;
  gap: 8px 16px;
}
.review-heading > p,
.review-heading h2 {
  grid-column: 1;
}
.review-heading .usage-label {
  grid-column: 2;
  grid-row: 1 / 3;
  align-self: center;
}
.review-heading > p {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.review-heading h2 {
  font-size: var(--fs-xl);
  font-weight: var(--fw-semibold);
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.review-heading h2:focus {
  outline: none;
}
.usage-label {
  padding: 5px 9px;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--accent-soft);
  font-size: var(--fs-xs);
}
.usage-label[data-usage='rejected'],
.usage-label[data-usage='deleting'] {
  background: var(--danger-soft);
  color: var(--danger);
}
.version-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding: 12px 0;
  border-block: 1px solid var(--border-subtle);
}
.version-strip > div {
  display: grid;
  gap: 8px;
  align-content: start;
  padding-inline: 18px;
  border-inline-start: 1px solid var(--border-subtle);
}
.version-strip > div:first-child {
  padding-inline-start: 0;
  border: 0;
}
.version-strip span {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
.version-strip strong {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
}
.version-strip button {
  justify-self: start;
  border: 0;
  padding: 3px 0;
  background: transparent;
  color: var(--accent);
  font: inherit;
  font-size: var(--fs-xs);
  cursor: pointer;
}
.review-note {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.review-feedback {
  color: var(--accent);
  font-size: var(--fs-sm);
  line-height: 1.7;
}
.review-sections,
.review-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.candidate-heading h3 {
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
}
.candidate-heading h3 span {
  margin-left: 8px;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  font-weight: var(--fw-normal);
}
.review-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}
.editor-pane,
.comparison-pane {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.comparison-pane {
  padding-inline-start: 24px;
  border-inline-start: 1px solid var(--border-subtle);
  max-height: 78vh;
  overflow-y: auto;
}
.editor-pane label,
.decision-confirmation label {
  display: grid;
  gap: 9px;
  font-size: var(--fs-sm);
}
.editor-pane input,
.editor-pane textarea,
.comparison-select select,
.decision-confirmation textarea {
  width: 100%;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: var(--surface-raised);
  font: inherit;
  font-size: var(--fs-sm);
}
.editor-pane textarea {
  min-height: 48vh;
  resize: vertical;
  line-height: 1.8;
  font-family: var(--mono-font);
}
.editor-pane input:read-only,
.editor-pane textarea:read-only {
  background: var(--surface-sunken);
}
.comparison-select {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: var(--fs-xs);
  white-space: nowrap;
  color: var(--text-secondary);
}
.comparison-select select {
  flex: 1;
  padding: 9px;
}
.decision-bar {
  border-top: 1px solid var(--border-subtle);
  padding-top: 20px;
}
.decision-confirmation {
  display: grid;
  gap: 16px;
  padding: 16px;
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  scroll-margin-top: 90px;
}
.mobile-pane-switch {
  display: none;
}
@media (max-width: 800px) {
  .review-columns {
    grid-template-columns: minmax(0, 1fr);
  }
  .mobile-pane-switch {
    display: flex;
    gap: 8px;
  }
  .editor-pane[data-mobile-visible='false'],
  .comparison-pane[data-mobile-visible='false'] {
    display: none;
  }
  .comparison-pane {
    border: 0;
    padding: 0;
    max-height: none;
  }
  .version-strip > div {
    padding-inline: 10px;
  }
  .review-heading h2 {
    font-size: var(--fs-lg);
  }
  .editor-pane textarea {
    min-height: 42vh;
  }
}
</style>
