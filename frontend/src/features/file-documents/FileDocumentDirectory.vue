<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { BookOpenText, FileUp, RefreshCw } from '@lucide/vue'
import type { FileDocumentDto } from '@/api/file-documents'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import { useFileDocuments } from './useFileDocuments'

const files = useFileDocuments()
const emit = defineEmits<{ 'read-document': [item: FileDocumentDto, trigger: HTMLElement] }>()
const editorOpen = ref(false)
const target = ref<FileDocumentDto>()
const deleting = ref<FileDocumentDto>()
const knowledgeBaseId = ref('')
const selectedFile = ref<File>()
const fileInput = ref<HTMLInputElement>()
const localError = ref<string>()
const maxSizeLabel = computed(() =>
  files.maxFileBytes.value ? `${files.maxFileBytes.value / 1024 / 1024} MiB` : '加载中',
)

async function openEditor(item?: FileDocumentDto) {
  target.value = item
  selectedFile.value = undefined
  localError.value = undefined
  editorOpen.value = true
  await nextTick()
  if (fileInput.value) {
    fileInput.value.value = ''
    fileInput.value.focus()
  }
}

async function save() {
  localError.value = undefined
  const file = selectedFile.value
  if (!file) {
    localError.value = '请选择一个 txt 或 md 文件。'
    return
  }
  if (!target.value && !knowledgeBaseId.value) {
    localError.value = '请选择文件所属的知识库。'
    return
  }
  if (!/\.(txt|md)$/i.test(file.name)) {
    localError.value = '仅支持 txt 和 md 文件。'
    return
  }
  if (!files.maxFileBytes.value || file.size > files.maxFileBytes.value) {
    localError.value = `文件不能超过 ${maxSizeLabel.value}。`
    return
  }
  if ((await files.save(file, knowledgeBaseId.value, target.value)) || files.needsReselect.value)
    editorOpen.value = false
}

function read(item: FileDocumentDto, event: MouseEvent) {
  emit('read-document', item, event.currentTarget as HTMLElement)
}

function statusLabel(item: FileDocumentDto): string {
  if (item.deletion_pending) return '删除未完成'
  return { pending: '等待索引', processing: '正在索引', indexed: '可检索', failed: '索引失败' }[
    item.processing_status
  ]
}

async function confirmDelete() {
  if (deleting.value && ((await files.remove(deleting.value)) || files.needsReselect.value))
    deleting.value = undefined
}
</script>

<template>
  <section class="file-directory" aria-label="文件资料">
    <div class="files-toolbar">
      <p>上传文本资料，在知识库中检索和引用。</p>
      <div class="file-actions">
        <BaseButton variant="outline" :disabled="files.busy.value" @click="files.refresh()"
          ><RefreshCw :size="15" />刷新状态</BaseButton
        >
        <BaseButton :disabled="files.busy.value" @click="openEditor()"
          ><FileUp :size="16" />上传文件</BaseButton
        >
      </div>
    </div>
    <form v-if="editorOpen" class="file-editor" @submit.prevent="save">
      <h2>{{ target ? `替换文件：${target.title}` : '上传文件' }}</h2>
      <p v-if="target">
        所属知识库：{{ target.knowledge_base_name }}。替换会保留同一篇文档，已有引用仍指向它。
      </p>
      <label v-else
        >所属知识库
        <select v-model="knowledgeBaseId" :disabled="files.busy.value" required>
          <option value="" disabled>请选择知识库</option>
          <option v-for="item in files.knowledgeBases.value" :key="item.id" :value="item.id">
            {{ item.name }}
          </option>
        </select>
      </label>
      <label
        >文本文件
        <input
          ref="fileInput"
          type="file"
          accept=".txt,.md,text/plain,text/markdown"
          :disabled="files.busy.value"
          @change="selectedFile = ($event.target as HTMLInputElement).files?.[0]"
        />
      </label>
      <p class="file-hint">
        支持 UTF-8 编码的 .txt、.md，单文件最大 {{ maxSizeLabel }}。保存后需等待索引完成。
      </p>
      <BaseCallout
        v-if="localError || files.directoryError.value"
        tone="danger"
        :description="localError || files.directoryError.value || ''"
      />
      <div class="file-actions">
        <BaseButton
          type="submit"
          :loading="files.busy.value"
          :disabled="!files.maxFileBytes.value || !!files.directoryError.value"
          >{{ target ? '确认替换' : '保存文件' }}</BaseButton
        >
        <BaseButton variant="outline" :disabled="files.busy.value" @click="editorOpen = false"
          >取消</BaseButton
        >
      </div>
    </form>
    <BaseCallout
      v-if="files.actionError.value"
      tone="danger"
      :description="files.actionError.value"
    />
    <p v-if="files.feedback.value" class="file-feedback" role="status">
      {{ files.feedback.value }}
    </p>
    <BaseCallout v-if="deleting" tone="neutral">
      <p>确认删除「{{ deleting.title }}」及其索引？已有回答会保留，但将无法再打开这篇原文。</p>
      <template #actions>
        <BaseButton :loading="files.busy.value" @click="confirmDelete">确认删除</BaseButton>
        <BaseButton variant="outline" :disabled="files.busy.value" @click="deleting = undefined"
          >取消</BaseButton
        >
      </template>
    </BaseCallout>
    <BaseCallout v-if="files.loadError.value" tone="danger" :description="files.loadError.value" />
    <p v-if="files.loading.value" role="status"><BaseSpinner :size="18" />正在加载文件资料</p>
    <p v-else-if="!files.items.value.length && !files.loadError.value" class="files-empty">
      还没有上传文档。选择知识库，上传第一份文本资料。
    </p>
    <table v-if="files.items.value.length" class="file-table">
      <caption class="sr-only">
        上传文件、所属知识库和处理状态
      </caption>
      <thead>
        <tr>
          <th>文档</th>
          <th>知识库</th>
          <th>处理状态</th>
          <th>更新时间</th>
          <th><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in files.items.value" :key="item.document_id">
          <td class="file-title">
            <strong>{{ item.title }}</strong
            ><span
              >{{ item.upload_filename }} ·
              {{ item.mime_type === 'text/markdown' ? 'Markdown' : '文本' }}</span
            >
          </td>
          <td>
            {{ item.knowledge_base_name }}<small v-if="!item.knowledge_base_active">已停用</small>
          </td>
          <td>
            <span
              class="file-status"
              :data-status="item.deletion_pending ? 'failed' : item.processing_status"
              >{{ statusLabel(item) }}</span
            ><small v-if="item.deletion_error || item.processing_error">{{
              item.deletion_error || item.processing_error
            }}</small>
          </td>
          <td class="file-date">{{ new Date(item.updated_at).toLocaleString('zh-CN') }}</td>
          <td>
            <div class="file-actions">
              <BaseButton variant="ghost" size="sm" @click="read(item, $event)"
                ><BookOpenText :size="15" />查看</BaseButton
              >
              <BaseButton
                variant="ghost"
                size="sm"
                :disabled="files.busy.value || !item.knowledge_base_active || item.deletion_pending"
                @click="openEditor(item)"
                >替换文件</BaseButton
              >
              <BaseButton
                v-if="item.processing_status === 'failed' && !item.deletion_pending"
                variant="ghost"
                size="sm"
                :disabled="files.busy.value || !item.knowledge_base_active"
                @click="files.retry(item)"
                >重试索引</BaseButton
              >
              <BaseButton
                variant="ghost"
                size="sm"
                :disabled="files.busy.value"
                @click="deleting = item"
                >{{ item.deletion_pending ? '继续删除' : '删除' }}</BaseButton
              >
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="files.offset.value > 0 || files.hasMore.value" class="files-pagination">
      <BaseButton
        variant="outline"
        :disabled="files.offset.value === 0 || files.busy.value"
        @click="files.offset.value -= 25"
        >上一页</BaseButton
      >
      <span>第 {{ files.offset.value / 25 + 1 }} 页</span>
      <BaseButton
        variant="outline"
        :disabled="!files.hasMore.value || files.busy.value"
        @click="files.offset.value += 25"
        >下一页</BaseButton
      >
    </div>
  </section>
</template>

<style scoped>
.file-directory {
  display: grid;
  gap: 20px;
  min-width: 0;
}
.files-toolbar,
.files-pagination {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
}
.files-toolbar > p,
.file-hint,
.files-empty {
  color: var(--text-secondary);
  font-size: 0.88rem;
  line-height: 1.7;
}
.file-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.file-editor {
  display: grid;
  gap: 14px;
  padding: 22px;
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}
.file-editor h2 {
  font-size: 1rem;
  overflow-wrap: anywhere;
}
.file-editor > p {
  overflow-wrap: anywhere;
}
.file-editor label {
  display: grid;
  gap: 8px;
  font-size: 0.85rem;
}
.file-editor select,
.file-editor input {
  width: 100%;
  min-width: 0;
  max-width: 520px;
  padding: 10px;
  color: var(--text-primary);
  background: var(--surface-base);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  font: inherit;
}
.file-feedback {
  color: var(--accent);
  font-size: 0.88rem;
}
.file-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.file-table th,
.file-table td {
  padding: 18px 12px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--border-subtle);
  overflow-wrap: anywhere;
  font-size: 0.85rem;
}
.file-table th {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 500;
}
.file-table th:first-child {
  width: 25%;
}
.file-table th:last-child {
  width: 28%;
}
.file-title strong {
  display: block;
  font-weight: 650;
  color: var(--text-primary);
  margin-bottom: 7px;
}
.file-title span,
.file-table small,
.file-date {
  color: var(--text-secondary);
  font-size: 0.75rem;
}
.file-table small {
  display: block;
  margin-top: 7px;
  line-height: 1.6;
}
.file-status {
  display: inline-block;
  padding: 4px 7px;
  background: var(--surface-sunken);
  border-radius: var(--radius-sm);
}
.file-status[data-status='indexed'] {
  color: var(--accent);
  background: var(--accent-soft);
}
.file-status[data-status='failed'] {
  color: var(--danger);
  background: var(--danger-soft);
}
.files-pagination {
  justify-content: flex-end;
  font-size: 0.8rem;
}
@media (max-width: 800px) {
  .file-table thead {
    display: none;
  }
  .file-table tbody {
    display: grid;
    gap: 16px;
  }
  .file-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 12px;
  }
  .file-table td {
    border: 0;
    padding: 8px 4px;
  }
  .file-table td:first-child,
  .file-table td:last-child {
    grid-column: 1 / -1;
  }
  .file-editor {
    padding: 16px;
  }
}
</style>
