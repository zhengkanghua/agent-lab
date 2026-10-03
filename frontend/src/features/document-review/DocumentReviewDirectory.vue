<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { CircleAlert, RefreshCw } from '@lucide/vue'
import { listKnowledgeBases } from '@/api/knowledge-bases'
import { listManagedDocuments, type ReviewFilters, REVIEW_PAGE_SIZE } from '@/api/document-review'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BasePager from '@/shared/ui/BasePager.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import {
  isProcessing,
  processingLabel,
  processingStates,
  usageLabel,
} from '@/shared/model/document-processing'
import { reviewError } from './presentation'
import { formatDateTime } from '@/shared/model/datetime'

const emit = defineEmits<{ open: [documentId: string] }>()
const filters = reactive<ReviewFilters>({ knowledgeBaseId: '', sourceKind: '', state: '' })
const offset = ref(0)
watch(filters, () => {
  offset.value = 0
})

/* 三个下拉是即生效的筛选。给一枚「重置筛选」：一是不用逐个改回全部，二是它的存在本身
   就说明这几个控件是筛选而不是待提交的表单。 */
const hasFilters = computed(
  () => filters.knowledgeBaseId !== '' || filters.sourceKind !== '' || filters.state !== '',
)

function resetFilters(): void {
  filters.knowledgeBaseId = ''
  filters.sourceKind = ''
  filters.state = ''
}
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
    <div class="review-toolbar">
      <p>统一检查上传文件与 FreshRSS 的解析结果，异常资料在这里修正。</p>
      <div class="review-actions">
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
        >知识库<BaseSelect v-model="filters.knowledgeBaseId">
          <option value="">全部知识库</option>
          <option v-for="item in directory.data.value" :key="item.id" :value="item.id">
            {{ item.name }}{{ item.is_active ? '' : '（已停用）' }}
          </option>
        </BaseSelect></label
      >
      <label
        >来源<BaseSelect v-model="filters.sourceKind">
          <option value="">全部来源</option>
          <option value="file">上传文件</option>
          <option value="freshrss">FreshRSS</option>
        </BaseSelect></label
      >
      <label
        >处理状态<BaseSelect v-model="filters.state">
          <option value="">全部状态</option>
          <option v-for="[value, label] in processingStates" :key="value" :value="value">
            {{ label }}
          </option>
        </BaseSelect></label
      >
    </div>
    <div class="filter-actions">
      <BaseButton v-if="hasFilters" variant="ghost" size="sm" @click="resetFilters"
        >重置筛选</BaseButton
      >
    </div>
    <BaseCallout
      v-if="directory.isError.value"
      tone="danger"
      description="知识库筛选目录加载失败。"
    >
      <template #icon><CircleAlert :size="16" aria-hidden="true" /></template>
      <template #actions>
        <BaseButton size="sm" variant="outline" @click="directory.refetch()">
          <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>
    <!-- 失败横幅里直接给重试：文案说的动作与按钮上的字对得上（此前横幅只说
         「请刷新后重试」，而唯一的重试入口是顶部那枚叫「刷新状态」的键）。 -->
    <BaseCallout
      v-if="query.error.value"
      tone="danger"
      :description="reviewError(query.error.value)"
    >
      <template #icon><CircleAlert :size="16" aria-hidden="true" /></template>
      <template #actions>
        <BaseButton
          size="sm"
          variant="outline"
          :disabled="query.isFetching.value"
          @click="query.refetch()"
        >
          <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>
    <!-- 三态行归共享层（styles/components/directory.css），与其余四个目录同一套。 -->
    <div v-if="query.isPending.value" class="directory-state" role="status">
      <BaseSpinner :size="20" />正在读取文档目录
    </div>
    <!-- 空态分两句说。「一个文档都没有」和「筛完没有」是两件事，下一步也完全不同：
         前者要去上传或绑定来源，后者放宽筛选就行（工具栏那枚「重置筛选」只在有筛选时
         才出现）。原来不分，第一次进来的人会以为自己筛错了，而他一进来根本没设过筛选。 -->
    <div v-else-if="!query.data.value?.items.length && !query.error.value" class="directory-state">
      <template v-if="hasFilters">
        当前筛选下没有文档。放宽或重置筛选，或换一个知识库再看。
      </template>
      <template v-else>
        还没有文档。先在文件资料页上传 MD、TXT 文件，或在来源管理里绑定订阅源。
      </template>
    </div>
    <table v-if="query.data.value?.items.length" class="review-table">
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
            ><small>{{ formatDateTime(item.updated_at) }}</small>
          </td>
          <td>
            <span class="cell-label">归属与来源</span>
            {{ item.knowledge_base_name
            }}<small>{{ item.source_kind === 'file' ? '上传文件' : 'FreshRSS' }}</small>
          </td>
          <td>
            <span class="cell-label">处理与使用状态</span>
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
    <BasePager
      v-if="offset > 0 || query.data.value?.has_more"
      :page="offset / REVIEW_PAGE_SIZE + 1"
      :has-previous="offset > 0"
      :has-more="query.data.value?.has_more ?? false"
      :busy="query.isFetching.value"
      @previous="offset -= REVIEW_PAGE_SIZE"
      @next="offset += REVIEW_PAGE_SIZE"
    />
  </section>
</template>

<style scoped>
.review-directory {
  display: grid;
  gap: 22px;
  min-width: 0;
}
/* 这一条叫 review-toolbar 而不是 directory-toolbar：后者是共享层里「左侧目录名 +
   共几条，右侧一组键，下面一条分隔线」的那套皮（知识库目录、来源管理在用）。
   这里其实是「一行说明 + 筛选与操作」，没有最小高度也没有分隔线，同名不同义——
   同名的话，共享层一改，这一页会被顺带染色。 */
.review-toolbar,
.review-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.review-toolbar > p {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: 1.7;
}
.filter-actions {
  display: flex;
  justify-content: flex-end;
  min-height: 28px;
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
/* 三个下拉的皮肤归 BaseSelect（同宽同高同描边、悬停加深、聚焦转强调色）；
   它们本来就是「一行一个的筛选字段」，正是那个组件的场景，原先各写一份
   等于把同样的盒子抄了第三遍，还漏掉了悬停与过渡。这里只留布局：
   删掉 min-width 的话，长知识库名会把这一列撑破格。 */
.directory-filters select {
  min-width: 0;
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

/* 表头在窄屏整行隐藏之后，这两格只剩「技术资料 / 上传文件」「待审核 / 已采用版本可用」，
   分不清哪个是知识库、哪个是来源，也看不出「待审核」说的是什么状态。移动态补标签
   （与账号目录 UserAccountRow 的 .cell-label 同一做法，宽度条件与表头消失严格对齐）。 */
.cell-label {
  display: none;
}
.processing-label[data-attention='true'] {
  color: var(--danger);
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

  .cell-label {
    display: block;
    margin-bottom: 2px;
    color: var(--text-tertiary);
    font-weight: var(--fw-semibold);
  }
}
</style>
