<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { listKnowledgeBases } from '@/api/knowledge-bases'
import { listManagedDocuments, type ReviewFilters } from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import {
  isProcessing,
  processingLabel,
  processingStates,
  usageLabel,
} from '@/shared/model/document-processing'
import { reviewError } from './presentation'

const emit = defineEmits<{ open: [documentId: string] }>()
const filters = reactive<ReviewFilters>({ knowledgeBaseId: '', sourceKind: '', state: '' })
const offset = ref(0)
watch(filters, () => {
  offset.value = 0
})
const directory = useQuery({
  queryKey: ['review-knowledge-bases'],
  queryFn: () => listKnowledgeBases(true),
  retry: false,
})
const query = useQuery({
  queryKey: computed(() => ['managed-documents', { ...filters }, offset.value]),
  queryFn: ({ signal }) => listManagedDocuments(filters, offset.value, signal),
  retry: false,
  refetchInterval: (query) =>
    query.state.status !== 'error' &&
    query.state.data?.items.some((item) => isProcessing(item.processing_state))
      ? 5000
      : false,
})
</script>

<template>
  <section class="review-directory" aria-label="文档处理列表">
    <div class="directory-toolbar">
      <p>统一检查上传文件与 FreshRSS 的解析结果，异常资料在这里修正。</p>
      <div class="directory-actions">
        <BaseButton variant="outline" :disabled="query.isFetching.value" @click="query.refetch()"
          >刷新状态</BaseButton
        >
        <BaseButton variant="primary" :to="{ name: 'admin', params: { section: 'files' } }"
          >上传文件</BaseButton
        >
      </div>
    </div>
    <div class="directory-filters">
      <label
        >知识库<select v-model="filters.knowledgeBaseId">
          <option value="">全部知识库</option>
          <option v-for="item in directory.data.value" :key="item.id" :value="item.id">
            {{ item.name }}{{ item.is_active ? '' : '（已停用）' }}
          </option>
        </select></label
      >
      <label
        >来源<select v-model="filters.sourceKind">
          <option value="">全部来源</option>
          <option value="file">上传文件</option>
          <option value="freshrss">FreshRSS</option>
        </select></label
      >
      <label
        >处理状态<select v-model="filters.state">
          <option value="">全部状态</option>
          <option v-for="[value, label] in processingStates" :key="value" :value="value">
            {{ label }}
          </option>
        </select></label
      >
    </div>
    <BaseCallout
      v-if="directory.isError.value"
      tone="danger"
      description="知识库筛选目录加载失败。"
    >
      <template #actions
        ><BaseButton @click="directory.refetch()">重新加载目录</BaseButton></template
      >
    </BaseCallout>
    <p v-if="query.isPending.value" role="status">正在加载文档…</p>
    <BaseCallout
      v-else-if="query.error.value"
      tone="danger"
      :description="reviewError(query.error.value)"
    />
    <p v-else-if="!query.data.value?.items.length" class="empty">
      当前筛选下没有文档。可以调整筛选，或上传 MD、TXT 文件。
    </p>
    <table v-else class="review-table">
      <caption class="sr-only">
        文档的已采用状态与候选处理进度
      </caption>
      <thead>
        <tr>
          <th>文档</th>
          <th>归属与来源</th>
          <th>处理与使用状态</th>
          <th><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in query.data.value?.items" :key="item.document_id">
          <td>
            <strong>{{ item.title || '未命名资料' }}</strong
            ><small>{{ new Date(item.updated_at).toLocaleString('zh-CN') }}</small>
          </td>
          <td>
            {{ item.knowledge_base_name
            }}<small>{{ item.source_kind === 'file' ? '上传文件' : 'FreshRSS' }}</small>
          </td>
          <td>
            <span
              class="processing-label"
              :data-attention="!!item.error_code || item.processing_state === 'review'"
              >{{ processingLabel(item.processing_state) }}</span
            >
            <small>{{
              usageLabel(item.usage_status, !!item.current_version_id, item.knowledge_base_active)
            }}</small>
          </td>
          <td>
            <BaseButton
              variant="ghost"
              size="sm"
              :aria-label="'查看与审核：' + (item.title || '未命名资料')"
              @click="emit('open', item.document_id)"
              >查看与审核</BaseButton
            >
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="offset > 0 || query.data.value?.has_more" class="directory-pagination">
      <BaseButton
        variant="outline"
        :disabled="offset === 0 || query.isFetching.value"
        @click="offset -= 25"
        >上一页</BaseButton
      >
      <span>第 {{ offset / 25 + 1 }} 页</span>
      <BaseButton
        variant="outline"
        :disabled="!query.data.value?.has_more || query.isFetching.value"
        @click="offset += 25"
        >下一页</BaseButton
      >
    </div>
  </section>
</template>

<style scoped>
.review-directory {
  display: grid;
  gap: 22px;
  min-width: 0;
}
.directory-toolbar,
.directory-actions,
.directory-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.directory-toolbar > p,
.empty {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: 1.7;
}
.directory-filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.directory-filters label {
  display: grid;
  gap: 8px;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.directory-filters select {
  padding: 11px;
  min-width: 0;
  color: var(--text-primary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
  font: inherit;
  font-size: var(--fs-sm);
}
.review-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
.review-table th,
.review-table td {
  padding: 18px 12px;
  border-bottom: 1px solid var(--border-subtle);
  text-align: left;
  vertical-align: top;
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}
.review-table th {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  font-weight: var(--fw-normal);
}
.review-table th:first-child {
  width: 34%;
}
.review-table th:last-child {
  width: 16%;
}
.review-table strong {
  font-weight: var(--fw-semibold);
}
.review-table small {
  display: block;
  margin-top: 8px;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: 1.6;
}
.processing-label {
  color: var(--accent);
  font-size: var(--fs-xs);
}
.processing-label[data-attention='true'] {
  color: var(--danger);
}
.directory-pagination {
  justify-content: flex-end;
  font-size: var(--fs-sm);
}
@media (max-width: 800px) {
  .directory-filters {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .directory-filters label {
    grid-template-columns: 70px minmax(0, 1fr);
    align-items: center;
  }
  .review-table thead {
    display: none;
  }
  .review-table tr {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border-bottom: 1px solid var(--border-subtle);
    padding-block: 12px;
  }
  .review-table td {
    border: 0;
    padding: 8px 4px;
  }
  .review-table td:first-child {
    grid-column: 1 / -1;
  }
  .review-table td:last-child {
    grid-column: 1 / -1;
  }
}
</style>
