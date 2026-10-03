<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { BookOpenText, CircleAlert, FileUp, RefreshCw } from '@lucide/vue'
import { FILE_PAGE_SIZE, type FileDocumentDto } from '@/api/file-documents'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import BasePager from '@/shared/ui/BasePager.vue'
import { useFileDocuments } from './useFileDocuments'
import { processingLabel, usageLabel } from '@/shared/model/document-processing'
import { formatDateTime } from '@/shared/model/datetime'
import { requestConfirm } from '@/shared/composables/confirm'

const files = useFileDocuments()
const emit = defineEmits<{ 'read-document': [item: FileDocumentDto, trigger: HTMLElement] }>()
const editorOpen = ref(false)
const target = ref<FileDocumentDto>()
const knowledgeBaseId = ref('')
const selectedFile = ref<File>()
const fileInput = ref<HTMLInputElement>()
const localError = ref<string>()
/* 上界要等文件列表读回来才知道。没读回来时是 null，调用方一律不提「最大多少」——
   以前这里回落到字符串「加载中」，于是拼进句子里成了「单文件最大 加载中。」。 */
const maxSizeLabel = computed(() =>
  files.maxFileBytes.value ? `${files.maxFileBytes.value / 1024 / 1024} MiB` : null,
)

const fileHint = computed(() =>
  maxSizeLabel.value
    ? `支持 UTF-8 编码的 .txt、.md，单文件最大 ${maxSizeLabel.value}。保存后由后台解析，异常资料在文档审核中处理。`
    : '支持 UTF-8 编码的 .txt、.md。保存后由后台解析，异常资料在文档审核中处理。',
)

function onFileChange(event: Event): void {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0]
  localError.value = undefined
}

async function openEditor(item?: FileDocumentDto) {
  const keepSelection =
    editorOpen.value && files.needsReselect.value && item?.document_id === target.value?.document_id
  target.value = item
  if (!keepSelection) selectedFile.value = undefined
  files.needsReselect.value = false
  localError.value = undefined
  editorOpen.value = true
  await nextTick()
  if (fileInput.value) {
    if (!keepSelection) fileInput.value.value = ''
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
  if (!maxSizeLabel.value) {
    localError.value = '文件大小上限尚未读回来，请先刷新状态再提交。'
    return
  }
  if (file.size > files.maxFileBytes.value!) {
    localError.value = `文件不能超过 ${maxSizeLabel.value}。`
    return
  }
  if (await files.save(file, knowledgeBaseId.value, target.value)) editorOpen.value = false
}

function reselectTarget() {
  const current = files.items.value.find((item) => item.document_id === target.value?.document_id)
  if (current) {
    target.value = current
    files.needsReselect.value = false
    files.actionError.value = null
  }
}

function read(item: FileDocumentDto, event: MouseEvent) {
  emit('read-document', item, event.currentTarget as HTMLElement)
}

function statusLabel(item: FileDocumentDto): string {
  if (item.deletion_pending) return '删除未完成'
  return processingLabel(item.candidate_state)
}

/**
 * 删除一份文档。
 *
 * 确认这一步统一走 ConfirmDialog（原来是一条行内 Callout 常驻在列表上方）：
 * 全站所有不可恢复的动作共用同一个确认框，文案里点名删的是哪一份，确认键写「完整删除」
 * 而不是「确定」。删除失败后行上的按钮变成「继续删除」，重试入口不变。
 */
async function removeDocument(item: FileDocumentDto) {
  const confirmed = await requestConfirm({
    title: `完整删除「${item.title}」？`,
    description:
      '原件、草稿、已采用历史、审核结论及索引都会清除；已有回答保留，但无法再打开这篇原文。',
    confirmLabel: '完整删除',
    tone: 'danger',
  })
  if (confirmed) await files.remove(item)
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
        <!-- 上传是这页的主操作，实心强调色；「刷新状态」是次要动作，描边。 -->
        <BaseButton variant="primary" :disabled="files.busy.value" @click="openEditor()"
          ><FileUp :size="16" />上传文件</BaseButton
        >
      </div>
    </div>
    <form v-if="editorOpen" class="file-editor" @submit.prevent="save">
      <h2>{{ target ? `替换文件：${target.title}` : '上传文件' }}</h2>
      <p v-if="target" class="file-note">
        所属知识库：{{
          target.knowledge_base_name
        }}。替换保留文档身份，新结果采用成功后才更新正式版本。
      </p>
      <BaseField v-else id="upload-knowledge-base" label="所属知识库" required>
        <template #default="{ control }">
          <BaseSelect v-bind="control" v-model="knowledgeBaseId" :disabled="files.busy.value">
            <option value="" disabled>请选择知识库</option>
            <option v-for="item in files.knowledgeBases.value" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </BaseSelect>
        </template>
      </BaseField>
      <BaseField id="upload-file" label="文本文件" :hint="fileHint">
        <template #default="{ control }">
          <!-- 原生 file 控件铺满整块并透明化：它自己就是唯一可点、可聚焦、可访问的控件，
               下面两个 span 只是它的外观。不为它再挂第二枚按钮、也不加第二个 tab 停靠点。 -->
          <div class="file-picker" :class="{ 'is-disabled': files.busy.value }">
            <input
              v-bind="control"
              ref="fileInput"
              class="file-picker-input"
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              :disabled="files.busy.value"
              @change="onFileChange"
            />
            <span class="file-picker-face" aria-hidden="true">选择文件</span>
            <span class="file-picker-name" aria-hidden="true">{{
              selectedFile?.name ?? '未选择文件'
            }}</span>
          </div>
        </template>
      </BaseField>
      <BaseCallout
        v-if="localError || files.directoryError.value"
        tone="danger"
        :description="localError || files.directoryError.value || ''"
      />
      <div class="file-form-actions">
        <BaseButton v-if="files.needsReselect.value" variant="outline" @click="reselectTarget">
          使用刷新后的记录继续替换
        </BaseButton>
        <BaseButton variant="outline" :disabled="files.busy.value" @click="editorOpen = false"
          >取消</BaseButton
        >
        <BaseButton
          type="submit"
          variant="primary"
          :loading="files.busy.value"
          :disabled="
            !files.maxFileBytes.value || !!files.directoryError.value || files.needsReselect.value
          "
          >{{ target ? '确认替换' : '保存文件' }}</BaseButton
        >
      </div>
    </form>
    <BaseCallout
      v-if="files.actionError.value"
      tone="danger"
      :description="files.actionError.value"
    >
      <template #icon><CircleAlert :size="16" aria-hidden="true" /></template>
    </BaseCallout>
    <p v-if="files.feedback.value" class="file-feedback" role="status">
      {{ files.feedback.value }}
    </p>
    <!-- 失败横幅里直接给重试：文案说的动作与按钮上的字对得上（此前横幅只说
         「请刷新后重试」，而唯一的重试入口是右上角那枚叫「刷新状态」的键）。 -->
    <BaseCallout v-if="files.loadError.value" tone="danger" :description="files.loadError.value">
      <template #icon><CircleAlert :size="16" aria-hidden="true" /></template>
      <template #actions>
        <BaseButton
          size="sm"
          variant="outline"
          :disabled="files.busy.value"
          @click="files.refresh()"
        >
          <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>
    <!-- 三态行归共享层（styles/components/directory.css），与其余四个目录同一套。 -->
    <div v-if="files.loading.value" class="directory-state" role="status">
      <BaseSpinner :size="20" />正在读取文件资料
    </div>
    <div v-else-if="!files.items.value.length && !files.loadError.value" class="directory-state">
      还没有上传文档。选择知识库，上传第一份文本资料。
    </div>
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
            ><small>{{
              usageLabel(item.usage_status, !!item.current_version_id, item.knowledge_base_active)
            }}</small>
            <small v-if="item.deletion_error || item.candidate_error">{{
              item.deletion_error || item.candidate_error
            }}</small>
          </td>
          <td class="file-date">{{ formatDateTime(item.updated_at) }}</td>
          <td>
            <div class="file-actions">
              <BaseButton
                variant="ghost"
                size="sm"
                :disabled="
                  !item.current_version_id ||
                  !item.content_hash ||
                  item.usage_status !== 'active' ||
                  !item.knowledge_base_active ||
                  item.deletion_pending
                "
                @click="read(item, $event)"
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
                variant="ghost"
                size="sm"
                :to="{
                  name: 'admin',
                  params: { section: 'documents' },
                  query: { document: item.document_id },
                }"
                >查看与审核</BaseButton
              >
              <!-- 破坏性动作单独一档：danger 变体的浅红底 + 多留一段间距，与上面三个
                   中性动作拉开。以前它也是 ghost，只靠「删除」两个字区分。 -->
              <BaseButton
                variant="danger"
                size="sm"
                class="row-remove"
                :disabled="files.busy.value"
                @click="removeDocument(item)"
                >{{ item.deletion_pending ? '继续删除' : '删除' }}</BaseButton
              >
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <BasePager
      v-if="files.offset.value > 0 || files.hasMore.value"
      :page="files.offset.value / FILE_PAGE_SIZE + 1"
      :has-previous="files.offset.value > 0"
      :has-more="files.hasMore.value"
      :busy="files.busy.value"
      @previous="files.offset.value -= FILE_PAGE_SIZE"
      @next="files.offset.value += FILE_PAGE_SIZE"
    />
  </section>
</template>

<style scoped>
.file-directory {
  display: grid;
  gap: 20px;
  min-width: 0;
}
.files-toolbar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
}
.files-toolbar > p,
.file-note {
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.7;
}
.file-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  /* 四个动作（查看 / 替换文件 / 查看与审核 / 删除）合计约 300px，比这一列原来的
     可用宽度还宽，于是「删除」被挤到第二行、落在处理状态列下面，看起来像排版坏了。
     靠右收拢：即使真的换行，第二行也贴着右缘，读起来是一排工具而不是掉出来的按钮。 */
  justify-content: flex-end;
  gap: 6px;
}
/* 行内破坏性动作与上面几个中性动作再拉开一点。 */
.row-remove {
  margin-left: var(--space-2);
}
.file-editor {
  display: grid;
  gap: var(--space-4);
  max-width: 720px;
  padding: var(--space-5);
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}
.file-editor h2 {
  font-size: var(--fs-base);
  overflow-wrap: anywhere;
}
/* 字段外壳（标签 / 必填标记 / 说明 / aria 接线）归 BaseField，选择器归 BaseSelect。
   这里只剩文件选择器的外观与表单自己的收尾。 */

/* 原生 file 控件铺满整块并透明化：它自己就是唯一可点、可聚焦、可访问的控件，
   下面两个 span 只是它的外观（所以 aria-hidden）。这样既没有浏览器原生的
   英文「Choose File / No file chosen」，也不用为它加第二个 tab 停靠点。 */
.file-picker {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 42px;
  padding: 0 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
}
.file-picker:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
/* 忙碌期间这枚选择器是禁用的，但原先除了指针变 wait 之外和可用时一模一样：
   点下去什么都不会发生，看起来像坏了。压暗整块、连带里面那枚「选择文件」一起退后。 */
.file-picker.is-disabled {
  background: var(--surface-sunken);
  opacity: 0.6;
}
.file-picker.is-disabled .file-picker-face {
  color: var(--text-tertiary);
}
.file-picker-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}
.file-picker-input:disabled {
  cursor: wait;
}
.file-picker-face {
  flex: 0 0 auto;
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: var(--surface-sunken);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}
.file-picker-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 次左主右：原先提交键在左、取消在右，主操作反而是先读到的那一个。 */
.file-form-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-2);
}
.file-feedback {
  color: var(--accent);
  font-size: var(--fs-sm);
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
  font-size: var(--fs-sm);
}
.file-table th {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-normal);
}
/* 列宽按各行真实需要的宽度分配（2026-10 实测，表宽约 1018px）：
   操作列四个动作合计 308px（含间距与 .row-remove 的外边距），给 34% 才有余量；
   更新时间「2026-09-08 08:00:00」在 12px 下约 127px，给 16% 才不折行。
   之前是 25% / 中间均分 / 28%，两头都差几像素，结果是日期折成两行、
   删除键被挤到第二行落在处理状态列下面。 */
.file-table th:first-child {
  width: 21%;
}
.file-table th:nth-child(2) {
  width: 13%;
}
.file-table th:nth-child(3) {
  width: 16%;
}
.file-table th:nth-child(4) {
  width: 16%;
}
.file-table th:last-child {
  width: 34%;
}
.file-title strong {
  display: block;
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  margin-bottom: 7px;
}
.file-title span,
.file-table small,
.file-date {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
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
  /* 手机上操作区占满整行、四枚键本来就要折成两排，靠左读起来才是从上到下的顺序。 */
  .file-actions {
    justify-content: flex-start;
  }
  .file-editor {
    padding: 16px;
  }
}
</style>
