<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { Check, Library, Pencil, Plus, RefreshCw, Save, X } from '@lucide/vue'
import type { KnowledgeBaseDto } from '@/api/knowledge-bases'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import BaseTextarea from '@/shared/ui/BaseTextarea.vue'
import { useKnowledgeBases } from './useKnowledgeBases'

const {
  items,
  loadError,
  loading,
  refreshing,
  refresh,
  feedback,
  actionError,
  editorOpen,
  editingId,
  draft,
  fieldErrors,
  saveError,
  saving,
  openEditor,
  closeEditor,
  submit,
  setActive,
} = useKnowledgeBases()

const editor = ref<HTMLFormElement | null>(null)
let editorTrigger: HTMLElement | null = null

async function edit(event: MouseEvent, item?: KnowledgeBaseDto): Promise<void> {
  editorTrigger = event.currentTarget as HTMLElement
  openEditor(item)
  await nextTick()
  editor.value?.querySelector<HTMLInputElement>('input:not([disabled])')?.focus()
}

function changeActive(event: Event, item: KnowledgeBaseDto): void {
  const input = event.target as HTMLInputElement
  const requested = input.checked
  input.checked = item.is_active
  void setActive(item, requested)
}

watch(editorOpen, async (open) => {
  if (!open) {
    await nextTick()
    editorTrigger?.focus()
  }
})
</script>

<template>
  <section class="knowledge-directory" aria-label="知识库目录">
    <div class="directory-toolbar">
      <div class="directory-count">
        <Library :size="20" aria-hidden="true" />
        <span
          >知识库目录 <strong>{{ loading ? '-' : items.length }}</strong></span
        >
      </div>
      <div class="toolbar-actions">
        <BaseIconButton label="刷新知识库" :disabled="refreshing || saving" @click="refresh()">
          <RefreshCw :size="17" aria-hidden="true" />
        </BaseIconButton>
        <BaseButton v-show="!editorOpen" variant="primary" :disabled="saving" @click="edit($event)">
          <template #icon><Plus :size="17" aria-hidden="true" /></template>
          创建知识库
        </BaseButton>
      </div>
    </div>

    <form
      v-if="editorOpen"
      ref="editor"
      class="knowledge-editor"
      novalidate
      @submit.prevent="submit"
    >
      <div class="editor-heading">
        <h2>{{ editingId ? '编辑知识库' : '创建知识库' }}</h2>
        <BaseIconButton label="关闭知识库表单" :disabled="saving" @click="closeEditor">
          <X :size="18" aria-hidden="true" />
        </BaseIconButton>
      </div>
      <div class="editor-fields">
        <BaseField label="名称" required :error="fieldErrors.name">
          <template #default="{ control }">
            <BaseInput
              v-model="draft.name"
              v-bind="control"
              name="knowledge-base-name"
              maxlength="255"
              :disabled="saving"
            />
          </template>
        </BaseField>
        <BaseField label="稳定键" required :error="fieldErrors.key">
          <template #default="{ control }">
            <BaseInput
              v-model="draft.key"
              v-bind="control"
              class="key-input"
              name="knowledge-base-key"
              maxlength="64"
              :disabled="saving || !!editingId"
              autocomplete="off"
              autocapitalize="none"
              spellcheck="false"
            />
          </template>
        </BaseField>
        <BaseField class="description-field" label="说明" :error="fieldErrors.description">
          <template #default="{ control }">
            <BaseTextarea
              v-model="draft.description"
              v-bind="control"
              name="knowledge-base-description"
              maxlength="2000"
              :rows="3"
              :disabled="saving"
            />
          </template>
        </BaseField>
      </div>
      <div class="editor-footer">
        <label v-if="!editingId" class="active-control">
          <input v-model="draft.isActive" type="checkbox" :disabled="saving" />
          启用
        </label>
        <BaseButton type="submit" variant="primary" :loading="saving">
          <template #icon><Save :size="17" aria-hidden="true" /></template>
          {{ editingId ? '保存修改' : '确认创建' }}
        </BaseButton>
      </div>
      <BaseCallout v-if="saveError" tone="danger" :description="saveError" />
    </form>

    <p v-if="feedback" class="feedback" role="status">
      <Check :size="16" aria-hidden="true" />{{ feedback }}
    </p>
    <BaseCallout v-if="actionError" tone="danger" :description="actionError" />
    <BaseCallout v-if="loadError" tone="danger">
      <span>{{ loadError }}</span>
      <template #actions>
        <BaseButton size="sm" variant="outline" :disabled="refreshing" @click="refresh()">
          <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>
    <div v-if="loading" class="directory-state" role="status">
      <BaseSpinner :size="20" />正在读取知识库
    </div>
    <div v-else-if="!loadError && items.length === 0" class="directory-state">暂无知识库</div>

    <table v-if="items.length" class="knowledge-table" :aria-busy="refreshing">
      <caption class="sr-only">
        知识库配置，包括名称、稳定键、状态和编辑操作
      </caption>
      <thead>
        <tr>
          <th scope="col">知识库</th>
          <th scope="col">稳定键</th>
          <th scope="col">状态</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :data-knowledge-base-id="item.id">
          <td class="name-cell">
            <strong>{{ item.name }}</strong>
            <p>{{ item.description || '未填写说明' }}</p>
          </td>
          <td class="key-cell">
            <code>{{ item.key }}</code>
          </td>
          <td class="status-cell">
            <label class="active-control" :class="{ 'is-inactive': !item.is_active }">
              <input
                type="checkbox"
                role="switch"
                :checked="item.is_active"
                :aria-label="`启用知识库 ${item.name}`"
                :disabled="saving"
                @change="changeActive($event, item)"
              />
              <span>{{ item.is_active ? '已启用' : '已停用' }}</span>
            </label>
          </td>
          <td class="action-cell">
            <BaseIconButton
              :label="`编辑知识库 ${item.name}`"
              :disabled="saving"
              @click="edit($event, item)"
            >
              <Pencil :size="16" aria-hidden="true" />
            </BaseIconButton>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.knowledge-directory {
  container-type: inline-size;
  letter-spacing: 0;
}
.directory-toolbar,
.toolbar-actions,
.directory-count,
.editor-heading,
.editor-footer {
  display: flex;
  align-items: center;
  gap: 12px;
}
.directory-toolbar {
  min-height: 64px;
  justify-content: space-between;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border-subtle);
}
.directory-count {
  color: var(--text-secondary);
  font-size: 0.86rem;
}
.directory-count svg {
  color: var(--accent);
}
.directory-count strong {
  margin-left: 8px;
  font-family: var(--mono-font);
  font-weight: 600;
  color: var(--text-primary);
}
.knowledge-editor {
  padding: 22px 0;
  border-bottom: 1px solid var(--border-subtle);
}
.editor-heading {
  justify-content: space-between;
  margin-bottom: 18px;
}
.editor-heading h2 {
  font-size: 1rem;
  font-weight: 700;
}
.editor-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.description-field {
  grid-column: 1 / -1;
}
.key-input {
  font-family: var(--mono-font);
}
.editor-footer {
  justify-content: flex-end;
  margin-top: 18px;
}
.editor-footer .active-control {
  margin-right: auto;
}
.knowledge-editor > :last-child:not(.editor-footer) {
  margin-top: 16px;
}
.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--success);
  font-size: 0.8rem;
  overflow-wrap: anywhere;
  padding: 16px 0;
}
.feedback svg {
  flex: 0 0 auto;
}
.directory-state {
  min-height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 0.85rem;
}
.knowledge-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
th {
  padding: 18px 12px;
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 600;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}
th:first-child {
  width: 48%;
  padding-left: 0;
}
th:nth-child(2) {
  width: 26%;
}
th:nth-child(3) {
  width: 18%;
}
th:last-child {
  width: 8%;
  padding-right: 0;
}
td {
  padding: 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
  vertical-align: middle;
}
.name-cell {
  padding-left: 0;
  overflow-wrap: anywhere;
}
.name-cell strong {
  font-size: 0.9rem;
  font-weight: 650;
}
.name-cell p {
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: 0.78rem;
  line-height: 1.65;
  white-space: pre-wrap;
}
.key-cell code {
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.76rem;
  overflow-wrap: anywhere;
}
.active-control {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  color: var(--success);
  font-size: 0.78rem;
  white-space: nowrap;
}
.active-control input {
  width: 17px;
  height: 17px;
  margin: 0;
  accent-color: var(--accent);
  cursor: pointer;
}
.active-control input:disabled {
  cursor: wait;
}
.active-control.is-inactive {
  color: var(--text-tertiary);
}
.action-cell {
  padding-right: 0;
  text-align: right;
}
@container (max-width: 580px) {
  .directory-toolbar {
    flex-wrap: wrap;
  }
  .directory-count {
    font-size: 0.8rem;
  }
  .editor-fields {
    grid-template-columns: 1fr;
  }
  .knowledge-table thead {
    display: none;
  }
  .knowledge-table tbody,
  .knowledge-table tr {
    display: block;
  }
  .knowledge-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 0 12px;
    padding: 18px 0;
    border-bottom: 1px solid var(--border-subtle);
  }
  .knowledge-table td {
    padding: 0;
    border: 0;
  }
  .name-cell {
    grid-column: 1;
    grid-row: 1;
  }
  .key-cell {
    grid-column: 1;
    grid-row: 2;
    margin-top: 8px;
  }
  .status-cell {
    grid-column: 1;
    grid-row: 3;
    margin-top: 8px;
  }
  .action-cell {
    grid-column: 2;
    grid-row: 1;
    align-self: start;
  }
}
</style>
