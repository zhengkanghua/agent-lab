<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { Check, KeyRound, Pencil, Plus, RefreshCw, Save, ServerCog, X } from '@lucide/vue'
import { isProviderKind, type LlmProviderDto } from '@/api/llm-providers'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import BaseSwitch from '@/shared/ui/BaseSwitch.vue'
import PasswordInput from '@/shared/ui/PasswordInput.vue'
import { PROVIDER_KIND_LABELS } from './model/provider-kind'
import { useLlmProviders } from './useLlmProviders'

/* 上游渠道目录：新增、修改、停用一条渠道，不提供删除（与知识库的「停用保留数据」一致）。
 *
 * 凭据在这一页只有一个入口、没有任何出口：编辑时输入框永远是空的，留空就表示不改，
 * 列表里那一列只显示「已配置 / 未配置」。 */

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
  setEnabled,
} = useLlmProviders()

const editor = ref<HTMLFormElement | null>(null)
let editorTrigger: HTMLElement | null = null

async function edit(event: MouseEvent, item?: LlmProviderDto): Promise<void> {
  editorTrigger = event.currentTarget as HTMLElement
  openEditor(item)
  await nextTick()
  editor.value?.querySelector<HTMLInputElement>('input:not([disabled])')?.focus()
}

/* BaseSwitch 已经把「改原生 checkbox 再拨回父级的值」做完了，这里只把请求值交给 store。 */
function changeEnabled(requested: boolean, item: LlmProviderDto): void {
  void setEnabled(item, requested)
}

/* 原生 select 抛出的永远是字符串；只有契约里的两个接入类型才写进草稿，其余忽略。
   直接 v-model 到 LlmProviderKind 上的话，这一步的类型收窄就没地方做了。 */
function changeProvider(value: string): void {
  if (isProviderKind(value)) draft.provider = value
}

watch(editorOpen, async (open) => {
  if (!open) {
    await nextTick()
    editorTrigger?.focus()
  }
})

/* Esc 与「取消」走同一条路径，保存中不关（与那两枚键的禁用条件一致）。 */
function requestCloseEditor(): void {
  if (!saving.value) closeEditor()
}
</script>

<template>
  <section class="provider-directory" aria-label="上游渠道目录">
    <div class="directory-toolbar">
      <div class="directory-count">
        <ServerCog :size="20" aria-hidden="true" />
        <span>上游渠道 <strong>{{ loading ? '-' : items.length }}</strong></span>
      </div>
      <div class="toolbar-actions">
        <BaseIconButton label="刷新上游渠道" :disabled="refreshing || saving" @click="refresh()">
          <RefreshCw :size="17" aria-hidden="true" />
        </BaseIconButton>
        <BaseButton v-show="!editorOpen" variant="primary" :disabled="saving" @click="edit($event)">
          <template #icon><Plus :size="17" aria-hidden="true" /></template>
          新增渠道
        </BaseButton>
      </div>
    </div>

    <form
      v-if="editorOpen"
      ref="editor"
      class="provider-editor"
      novalidate
      @submit.prevent="submit"
      @keydown.esc="requestCloseEditor"
    >
      <div class="editor-heading">
        <h2>{{ editingId ? '编辑上游渠道' : '新增上游渠道' }}</h2>
        <BaseIconButton label="关闭渠道表单" :disabled="saving" @click="closeEditor">
          <X :size="18" aria-hidden="true" />
        </BaseIconButton>
      </div>
      <div class="editor-fields">
        <BaseField label="渠道名称" required :error="fieldErrors.name">
          <template #default="{ control }">
            <BaseInput
              v-model="draft.name"
              v-bind="control"
              name="llm-provider-name"
              maxlength="255"
              :disabled="saving"
            />
          </template>
        </BaseField>
        <BaseField label="接入类型" required>
          <template #default="{ control }">
            <BaseSelect
              :model-value="draft.provider"
              v-bind="control"
              name="llm-provider-kind"
              :disabled="saving"
              @update:model-value="changeProvider"
            >
              <option v-for="(label, kind) in PROVIDER_KIND_LABELS" :key="kind" :value="kind">
                {{ label }}
              </option>
            </BaseSelect>
          </template>
        </BaseField>
        <BaseField
          class="url-field"
          label="上游地址"
          required
          hint="OpenAI 兼容中转站通常要带 /v1 后缀。"
          :error="fieldErrors.baseUrl"
        >
          <template #default="{ control }">
            <BaseInput
              v-model="draft.baseUrl"
              v-bind="control"
              name="llm-provider-base-url"
              maxlength="2048"
              spellcheck="false"
              autocomplete="off"
              :disabled="saving"
            />
          </template>
        </BaseField>
        <BaseField
          label="凭据"
          :error="fieldErrors.credential"
          hint="留空表示不改动已存的凭据；凭据一旦保存就不会再显示出来。"
        >
          <template #default="{ control }">
            <PasswordInput
              v-model="draft.credential"
              v-bind="control"
              name="llm-provider-credential"
              autocomplete="new-password"
              :disabled="saving"
            />
          </template>
        </BaseField>
      </div>
      <div class="editor-footer">
        <label class="check-control">
          <input v-model="draft.enabled" type="checkbox" :disabled="saving" />
          <span>启用</span>
        </label>
        <!-- 次左主右：放弃编辑除了右上角那枚 X，也要有一枚明确的「取消」。 -->
        <BaseButton variant="outline" :disabled="saving" @click="closeEditor">取消</BaseButton>
        <BaseButton type="submit" variant="primary" :loading="saving">
          <template #icon><Save :size="17" aria-hidden="true" /></template>
          {{ editingId ? '保存修改' : '确认新增' }}
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
      <BaseSpinner :size="20" />正在读取上游渠道
    </div>
    <div v-else-if="!loadError && items.length === 0" class="directory-state">
      还没有上游渠道，用右上角的「新增渠道」加一条。
    </div>

    <table v-if="items.length" class="provider-table" :aria-busy="refreshing">
      <caption class="sr-only">
        上游渠道配置，包括名称、接入类型、凭据是否配置、状态和编辑操作
      </caption>
      <thead>
        <tr>
          <th scope="col">渠道</th>
          <th scope="col">接入类型</th>
          <th scope="col">凭据</th>
          <th scope="col">状态</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :data-llm-provider-id="item.id">
          <td class="name-cell">
            <strong>{{ item.name }}</strong>
            <p>{{ item.base_url }}</p>
          </td>
          <td class="kind-cell">{{ PROVIDER_KIND_LABELS[item.provider] }}</td>
          <td class="credential-cell">
            <span class="credential-state" :class="{ 'is-missing': !item.credential_configured }">
              <KeyRound :size="14" aria-hidden="true" />
              {{ item.credential_configured ? '已配置' : '未配置' }}
            </span>
          </td>
          <td class="status-td">
            <!-- flex 排在里层的 div 上，不能直接写在 <td> 上：td 变成 flex 容器就脱离表格
                 布局，行分隔线会在这一列断成两段。单元格自己保持 table-cell。 -->
            <div class="status-cell">
              <BaseSwitch
                :checked="item.enabled"
                :label="`启用上游渠道 ${item.name}`"
                :disabled="saving"
                @change="changeEnabled($event, item)"
              />
              <span class="status-text" :class="{ 'is-inactive': !item.enabled }">
                {{ item.enabled ? '已启用' : '已停用' }}
              </span>
            </div>
          </td>
          <td class="action-cell">
            <BaseIconButton
              :label="`编辑上游渠道 ${item.name}`"
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
.provider-directory {
  container-type: inline-size;
  letter-spacing: 0;
}
/* 目录工具栏（.directory-toolbar / .toolbar-actions / .directory-count）与三态行
   （.directory-state）都归共享层 directory.css，与另外几个目录页同一套。 */
.editor-heading,
.editor-footer {
  display: flex;
  align-items: center;
  gap: 12px;
}
.provider-editor {
  /* 内联表单不铺满整行：字段拉成一条线之外，头部那枚关闭键也会离标题太远。 */
  max-width: 720px;
  padding: 22px 0;
  border-bottom: 1px solid var(--border-subtle);
}
.editor-heading {
  justify-content: space-between;
  margin-bottom: 18px;
}
.editor-heading h2 {
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
}
.editor-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.url-field {
  grid-column: 1 / -1;
}
.editor-footer {
  justify-content: flex-end;
  margin-top: 18px;
}
.editor-footer .check-control {
  margin-right: auto;
}
.provider-editor > :last-child:not(.editor-footer) {
  margin-top: 16px;
}
.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 0;
  color: var(--success);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}
.feedback svg {
  flex: 0 0 auto;
}
.provider-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
th {
  padding: 18px 12px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}
th:first-child {
  width: 34%;
  padding-left: 0;
}
th:nth-child(2) {
  width: 16%;
}
th:nth-child(3) {
  width: 16%;
}
th:nth-child(4) {
  width: 26%;
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
.provider-table tbody tr {
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}
.provider-table tbody tr:hover {
  background: var(--surface-sunken);
}
.name-cell {
  padding-left: 0;
  overflow-wrap: anywhere;
}
.name-cell strong {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
}
.name-cell p {
  margin-top: 5px;
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.kind-cell {
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.credential-cell {
  font-size: var(--fs-xs);
}
/* 凭据状态：图标 + 文字，颜色区分「有 / 没有」。它只说有没有，不说能不能用。 */
.credential-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.credential-state svg {
  flex: 0 0 auto;
}
.credential-state.is-missing {
  color: var(--text-tertiary);
}
.status-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.status-text {
  color: var(--success);
  font-size: var(--fs-xs);
  white-space: nowrap;
}
.status-text.is-inactive {
  color: var(--text-tertiary);
}
.action-cell {
  padding-right: 0;
  text-align: right;
}
@container (max-width: 580px) {
  .editor-fields {
    grid-template-columns: 1fr;
  }
  .provider-table thead {
    display: none;
  }
  .provider-table tbody,
  .provider-table tr {
    display: block;
  }
  .provider-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 0 12px;
    padding: 18px 0;
    border-bottom: 1px solid var(--border-subtle);
  }
  .provider-table td {
    padding: 0;
    border: 0;
  }
  .name-cell {
    grid-column: 1;
    grid-row: 1;
  }
  .kind-cell {
    grid-column: 1;
    grid-row: 2;
    margin-top: 8px;
  }
  .credential-cell {
    grid-column: 1;
    grid-row: 3;
    margin-top: 8px;
  }
  .status-td {
    grid-column: 1;
    grid-row: 4;
    margin-top: 8px;
  }
  .action-cell {
    grid-column: 2;
    grid-row: 1;
    align-self: start;
  }
}
</style>
