<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import {
  getDocumentVersion,
  listDocumentCandidates,
  listDocumentDecisions,
  listDocumentVersions,
} from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import DocumentOriginal from './DocumentOriginal.vue'
import DocumentPreview from './DocumentPreview.vue'
import { processingLabel } from '@/shared/model/document-processing'

const props = defineProps<{ documentId: string; revision: number; filename: string }>()
const emit = defineEmits<{ select: [processingId: string] }>()
const kind = ref('versions')
const offset = ref(0)
const selectedVersion = ref<string>()
const versions = useQuery({
  queryKey: computed(() => ['document-versions', props.documentId, props.revision, offset.value]),
  queryFn: ({ signal }) => listDocumentVersions(props.documentId, offset.value, signal),
  enabled: computed(() => kind.value === 'versions'),
  retry: false,
})
const decisions = useQuery({
  queryKey: computed(() => ['document-decisions', props.documentId, props.revision, offset.value]),
  queryFn: ({ signal }) => listDocumentDecisions(props.documentId, offset.value, signal),
  enabled: computed(() => kind.value === 'reviews'),
  retry: false,
})
const candidates = useQuery({
  queryKey: computed(() => ['document-candidates', props.documentId, props.revision, offset.value]),
  queryFn: ({ signal }) => listDocumentCandidates(props.documentId, offset.value, signal),
  enabled: computed(() => kind.value === 'candidates'),
  retry: false,
})
const version = useQuery({
  queryKey: computed(() => ['document-version', props.documentId, selectedVersion.value]),
  queryFn: ({ signal }) => getDocumentVersion(props.documentId, selectedVersion.value!, signal),
  enabled: computed(() => !!selectedVersion.value && kind.value === 'versions'),
  retry: false,
})
const currentQuery = computed(() =>
  kind.value === 'versions' ? versions : kind.value === 'reviews' ? decisions : candidates,
)
const hasMore = computed(() => currentQuery.value.data.value?.has_more ?? false)
const loading = computed(() => currentQuery.value.isPending.value)
const loadError = computed(() => currentQuery.value.isError.value)
const empty = computed(() => !currentQuery.value.data.value?.items.length)
const decisionLabels: Record<string, string> = {
  adopt: '确认采用',
  reject: '拒绝使用',
  retry: '重新处理',
}
const snapshotBody = (value: Record<string, unknown>) =>
  typeof value.body === 'string' ? value.body : '该决定时尚无可用的解析正文。'

watch([kind, () => props.documentId], () => {
  offset.value = 0
  selectedVersion.value = undefined
})
</script>

<template>
  <section class="review-history" aria-label="版本与审核历史">
    <div class="history-tabs" role="group" aria-label="历史类型">
      <BaseButton
        v-for="[key, label] in [
          ['versions', '已采用版本'],
          ['reviews', '审核结论'],
          ['candidates', '处理记录'],
        ]"
        :key="key"
        :variant="kind === key ? 'outline' : 'ghost'"
        :aria-pressed="kind === key"
        @click="kind = key!"
        >{{ label }}</BaseButton
      >
    </div>
    <p v-if="loading" role="status">正在加载历史…</p>
    <BaseCallout v-else-if="loadError" tone="danger" description="历史加载失败，当前编辑仍保留。">
      <template #actions
        ><BaseButton variant="outline" @click="currentQuery.refetch()"
          >重新加载</BaseButton
        ></template
      >
    </BaseCallout>
    <p v-else-if="empty" class="history-note">暂无这类记录。</p>
    <ol v-else-if="kind === 'versions'" class="history-list">
      <li v-for="item in versions.data.value?.items" :key="item.version_id">
        <div>
          <strong>第 {{ item.revision }} 版 · {{ item.title }}</strong
          ><small>{{ new Date(item.created_at).toLocaleString('zh-CN') }}</small>
        </div>
        <BaseButton variant="ghost" size="sm" @click="selectedVersion = item.version_id"
          >查看版本</BaseButton
        >
      </li>
    </ol>
    <ol v-else-if="kind === 'reviews'" class="history-list decisions">
      <li v-for="item in decisions.data.value?.items" :key="item.review_id">
        <strong
          >{{ decisionLabels[item.decision] ?? item.decision }} ·
          {{ item.decision_source === 'automatic' ? '自动处理' : '人工处理' }}</strong
        >
        <small
          >{{ new Date(item.created_at).toLocaleString('zh-CN') }} · 草稿修订
          {{ item.candidate_revision }}</small
        >
        <p>{{ item.conclusion || '未填写附加结论' }}</p>
        <details>
          <summary>查看决定时的正文</summary>
          <pre>{{ snapshotBody(item.content_snapshot) }}</pre>
        </details>
      </li>
    </ol>
    <ol v-else class="history-list">
      <li v-for="item in candidates.data.value?.items" :key="item.processing_id">
        <div>
          <strong>{{ item.title || '未命名资料' }}</strong>
          <small
            >{{ processingLabel(item.state) }} ·
            {{ new Date(item.created_at).toLocaleString('zh-CN') }}</small
          >
        </div>
        <BaseButton variant="ghost" size="sm" @click="emit('select', item.processing_id)"
          >查看处理结果</BaseButton
        >
      </li>
    </ol>
    <div v-if="offset || hasMore" class="history-pagination">
      <BaseButton variant="outline" :disabled="offset === 0 || loading" @click="offset -= 25"
        >上一页</BaseButton
      >
      <span>第 {{ offset / 25 + 1 }} 页</span>
      <BaseButton variant="outline" :disabled="!hasMore || loading" @click="offset += 25"
        >下一页</BaseButton
      >
    </div>
    <template v-if="selectedVersion && kind === 'versions'">
      <p v-if="version.isPending.value" role="status">正在读取已采用版本…</p>
      <BaseCallout v-else-if="version.isError.value" tone="danger" description="版本读取失败。">
        <template #actions
          ><BaseButton variant="outline" @click="version.refetch()">重试读取</BaseButton></template
        >
      </BaseCallout>
      <article v-else-if="version.data.value" class="version-detail">
        <h3>已采用历史 · 第 {{ version.data.value.revision }} 版</h3>
        <p class="history-note">这是当时采用的正文和 Chunk，不会随当前草稿修改。</p>
        <DocumentOriginal
          :processing-id="version.data.value.processing_id"
          :filename="filename"
          stored
        />
        <DocumentPreview :preview="version.data.value.preview" />
      </article>
    </template>
  </section>
</template>

<style scoped>
.review-history {
  display: grid;
  gap: 20px;
  min-width: 0;
}
.history-tabs,
.history-pagination {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.history-pagination {
  justify-content: flex-end;
  font-size: var(--fs-sm);
}
.history-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.history-list > li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--border-subtle);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}
.history-list strong {
  font-weight: var(--fw-semibold);
}
.history-list small {
  display: block;
  margin-top: 7px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
.history-list.decisions > li {
  display: grid;
  gap: 8px;
}
.history-list summary {
  cursor: pointer;
  color: var(--accent);
  padding: 8px 0;
}
.history-list pre {
  padding: 12px;
  background: var(--surface-sunken);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  line-height: 1.8;
  max-height: 50vh;
  overflow-y: auto;
}
.history-note {
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.7;
}
.version-detail {
  display: grid;
  gap: 20px;
  padding-top: 12px;
  border-top: 2px solid var(--accent);
}
.version-detail h3 {
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
}
</style>
