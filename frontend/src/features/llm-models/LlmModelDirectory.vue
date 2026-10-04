<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { Check, Cpu, Pencil, Plus, RefreshCw, Save, Star, X } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import BaseSelect from '@/shared/ui/BaseSelect.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import BaseSwitch from '@/shared/ui/BaseSwitch.vue'
import type { LlmModelDto } from '@/api/llm-models'
import { modelLabel, useLlmModels } from './useLlmModels'

/* 可用模型目录：挂在某条上游渠道下面的模型，新增、修改、启停、设默认，不提供删除
 * （与渠道、知识库的「停用保留数据」一致）。
 *
 * 两处刻意的呈现：
 *
 * 1. 「可不可选」是**两个开关相与**的结果——模型自己的启用位，加所属渠道的启用位。所以渠道那
 *    一列在渠道停用时明确标出来：否则管理员会看到一条「已启用」的模型却怎么都选不到。
 * 2. 「默认」既是事实也是动作：是默认的那条挂胶囊，不是的那条给一枚「设为默认」。不可用的模型
 *    不能当默认（后端也会拒），这枚键在那两档下禁用并说明原因。
 */

const {
  items,
  providers,
  loadError,
  providersError,
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
  setDefault,
} = useLlmModels()

const editor = ref<HTMLFormElement | null>(null)
let editorTrigger: HTMLElement | null = null

async function edit(event: MouseEvent, item?: LlmModelDto): Promise<void> {
  editorTrigger = event.currentTarget as HTMLElement
  openEditor(item)
  await nextTick()
  editor.value?.querySelector<HTMLInputElement>('input:not([disabled])')?.focus()
}

/* BaseSwitch 已经把「改原生 checkbox 再拨回父级的值」做完了，这里只把请求值交给 store。 */
function changeEnabled(requested: boolean, item: LlmModelDto): void {
  void setEnabled(item, requested)
}

/* 原生 select 抛出的永远是字符串，直接把渠道 id 写进草稿。 */
function changeProvider(value: string): void {
  draft.providerId = value
}

/** 这条模型现在能不能被用户选到：自己启用 **且** 所属渠道也启用。 */
function isAvailable(item: LlmModelDto): boolean {
  return item.enabled && item.provider_enabled
}

function unavailableReason(item: LlmModelDto): string {
  if (!item.provider_enabled) return '所属渠道已停用，先启用渠道再设为默认。'
  if (!item.enabled) return '这条模型已停用，先启用它再设为默认。'
  return ''
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
  <section class="model-directory" aria-label="可用模型目录">
    <div class="directory-toolbar">
      <div class="directory-count">
        <Cpu :size="20" aria-hidden="true" />
        <span>可用模型 <strong>{{ loading ? '-' : items.length }}</strong></span>
      </div>
      <div class="toolbar-actions">
        <BaseIconButton label="刷新可用模型" :disabled="refreshing || saving" @click="refresh()">
          <RefreshCw :size="17" aria-hidden="true" />
        </BaseIconButton>
        <BaseButton v-show="!editorOpen" variant="primary" :disabled="saving" @click="edit($event)">
          <template #icon><Plus :size="17" aria-hidden="true" /></template>
          新增模型
        </BaseButton>
      </div>
    </div>

    <form
      v-if="editorOpen"
      ref="editor"
      class="model-editor"
      novalidate
      @submit.prevent="submit"
      @keydown.esc="requestCloseEditor"
    >
      <div class="editor-heading">
        <h2>{{ editingId ? '编辑可用模型' : '新增可用模型' }}</h2>
        <BaseIconButton label="关闭模型表单" :disabled="saving" @click="closeEditor">
          <X :size="18" aria-hidden="true" />
        </BaseIconButton>
      </div>
      <div class="editor-fields">
        <BaseField label="所属渠道" required :error="fieldErrors.providerId">
          <template #default="{ control }">
            <BaseSelect
              :model-value="draft.providerId"
              v-bind="control"
              name="llm-model-provider"
              :disabled="saving"
              @update:model-value="changeProvider"
            >
              <option value="" disabled>请选择上游渠道</option>
              <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                {{ provider.enabled ? provider.name : `${provider.name}（已停用）` }}
              </option>
            </BaseSelect>
          </template>
        </BaseField>
        <BaseField
          label="上游模型名"
          required
          hint="上游那一侧真实存在的模型名，同一渠道内不可重复。"
          :error="fieldErrors.upstreamModelName"
        >
          <template #default="{ control }">
            <BaseInput
              v-model="draft.upstreamModelName"
              v-bind="control"
              name="llm-model-upstream-name"
              maxlength="255"
              spellcheck="false"
              autocomplete="off"
              :disabled="saving"
            />
          </template>
        </BaseField>
        <BaseField
          label="展示名"
          hint="只用于界面；留空时模型选择器里显示上游模型名。"
          :error="fieldErrors.displayName"
        >
          <template #default="{ control }">
            <BaseInput
              v-model="draft.displayName"
              v-bind="control"
              name="llm-model-display-name"
              maxlength="255"
              :disabled="saving"
            />
          </template>
        </BaseField>
        <!-- 这一栏的提示不是客套话，见 spec 0001 的上下文策略：窗口填小了会提前压缩，
             而且一轮里单次工具输出能放多长也按它的比例算，工具返回会被截断。 -->
        <BaseField
          label="上下文窗口"
          required
          hint="填你确认过的最小值，不是「保守就行」：压缩什么时候触发按它的比例算，一轮里单次工具输出能放多长也取它的比例。填大了可能超过上游真实窗口、整轮失败；填小了会提前压缩并截断工具返回的内容。"
          :error="fieldErrors.contextWindow"
        >
          <template #default="{ control }">
            <BaseInput
              v-model="draft.contextWindow"
              v-bind="control"
              type="number"
              min="1"
              step="1"
              name="llm-model-context-window"
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
    <!-- 读不到渠道目录就没法挑「所属渠道」，这条失败要说出来，不能只表现成一个空下拉框。 -->
    <BaseCallout v-else-if="providersError" tone="danger" :description="providersError" />
    <div v-if="loading" class="directory-state" role="status">
      <BaseSpinner :size="20" />正在读取可用模型
    </div>
    <div v-else-if="!loadError && items.length === 0" class="directory-state">
      还没有可用模型，用右上角的「新增模型」加一条。
    </div>

    <table v-if="items.length" class="model-table" :aria-busy="refreshing">
      <caption class="sr-only">
        可用模型配置，包括名称、所属渠道、上下文窗口、默认与否、状态和编辑操作
      </caption>
      <thead>
        <tr>
          <th scope="col">模型</th>
          <th scope="col">所属渠道</th>
          <th scope="col">上下文窗口</th>
          <th scope="col">默认</th>
          <th scope="col">状态</th>
          <th scope="col"><span class="sr-only">操作</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :data-llm-model-id="item.id">
          <td class="name-cell">
            <strong>{{ modelLabel(item) }}</strong>
            <p v-if="item.display_name">{{ item.upstream_model_name }}</p>
          </td>
          <td class="provider-cell">
            <div class="provider-cell-body">
              <span>{{ item.provider_name }}</span>
              <span v-if="!item.provider_enabled" class="provider-state">渠道已停用</span>
            </div>
          </td>
          <td class="window-cell">{{ item.context_window }}</td>
          <td class="default-cell">
            <span v-if="item.is_default" class="status-chip is-on" role="status">默认</span>
            <BaseButton
              v-else
              size="sm"
              variant="outline"
              :disabled="saving || !isAvailable(item)"
              :title="unavailableReason(item) || undefined"
              @click="setDefault(item)"
            >
              <template #icon><Star :size="15" aria-hidden="true" /></template>
              设为默认
            </BaseButton>
          </td>
          <td class="status-td">
            <!-- flex 排在里层的 div 上，不能直接写在 <td> 上：td 变成 flex 容器就脱离表格
                 布局，行分隔线会在这一列断成两段。单元格自己保持 table-cell。 -->
            <div class="status-cell">
              <BaseSwitch
                :checked="item.enabled"
                :label="`启用可用模型 ${modelLabel(item)}`"
                :disabled="saving || item.is_default"
                :title="item.is_default ? '当前默认模型不能被停用' : undefined"
                @change="changeEnabled($event, item)"
              />
              <span class="status-text" :class="{ 'is-inactive': !item.enabled }">
                {{ item.enabled ? '已启用' : '已停用' }}
              </span>
            </div>
          </td>
          <td class="action-cell">
            <BaseIconButton
              :label="`编辑可用模型 ${modelLabel(item)}`"
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
.model-directory {
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
.model-editor {
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
.editor-footer {
  justify-content: flex-end;
  margin-top: 18px;
}
.editor-footer .check-control {
  margin-right: auto;
}
.model-editor > :last-child:not(.editor-footer) {
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
.model-table {
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
  width: 26%;
  padding-left: 0;
}
th:nth-child(2) {
  width: 18%;
}
th:nth-child(3) {
  width: 14%;
}
th:nth-child(4) {
  width: 18%;
}
th:nth-child(5) {
  width: 16%;
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
.model-table tbody tr {
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}
.model-table tbody tr:hover {
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
/* 渠道名与「已停用」标一行排不下时换行，不把渠道名挤断。layout 写在里层的 div 上，
   td 自己保持 table-cell：单元格拿到别的 display 就脱离表格布局，行分隔线会断开。 */
.provider-cell-body {
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}
.provider-state {
  display: block;
  margin-top: 5px;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}
.window-cell {
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: var(--fs-sm);
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
@container (max-width: 720px) {
  .editor-fields {
    grid-template-columns: 1fr;
  }
}
@container (max-width: 620px) {
  .model-table thead {
    display: none;
  }
  .model-table tbody,
  .model-table tr {
    display: block;
  }
  .model-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 0 12px;
    padding: 18px 0;
    border-bottom: 1px solid var(--border-subtle);
  }
  .model-table td {
    padding: 0;
    border: 0;
  }
  .name-cell {
    grid-column: 1;
    grid-row: 1;
  }
  .provider-cell {
    grid-column: 1;
    grid-row: 2;
    margin-top: 8px;
  }
  .window-cell {
    grid-column: 1;
    grid-row: 3;
    margin-top: 8px;
  }
  .default-cell {
    grid-column: 1;
    grid-row: 4;
    margin-top: 8px;
  }
  .status-td {
    grid-column: 1;
    grid-row: 5;
    margin-top: 8px;
  }
  .action-cell {
    grid-column: 2;
    grid-row: 1;
    align-self: start;
  }
}
</style>
