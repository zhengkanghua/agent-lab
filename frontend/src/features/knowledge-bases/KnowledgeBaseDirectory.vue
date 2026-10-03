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
import BaseSwitch from '@/shared/ui/BaseSwitch.vue'
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

/* BaseSwitch 已经把「改原生 checkbox 再拨回父级的值」做完了，这里只把请求值交给 store——
   原先本文件自己手写了一遍那段（与账号目录、任务目录同一个逻辑，那两处已收编）。 */
function changeActive(requested: boolean, item: KnowledgeBaseDto): void {
  void setActive(item, requested)
}

watch(editorOpen, async (open) => {
  if (!open) {
    await nextTick()
    editorTrigger?.focus()
  }
})

/* Esc 与「取消」走同一条路径，保存中不关（与那两枚键的禁用条件一致）。
   监听挂在 form 上而不是 document：useModalLayer 的 Esc 也是 document 级、
   且不拦截冒泡，挂 document 会在弹层开着时把两层一起关掉。 */
function requestCloseEditor(): void {
  if (!saving.value) closeEditor()
}
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
      @keydown.esc="requestCloseEditor"
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
        <BaseField class="description-field" label="说明（选填）" :error="fieldErrors.description">
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
        <label v-if="!editingId" class="check-control">
          <input v-model="draft.isActive" type="checkbox" :disabled="saving" />
          <span>启用</span>
        </label>
        <!-- 次左主右：原先只有一枚提交键，放弃编辑得靠右上角那枚 X。 -->
        <BaseButton variant="outline" :disabled="saving" @click="closeEditor">取消</BaseButton>
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
    <!-- 空态要说清下一步。原来只有「暂无知识库」四个字：这是一个后台管理页，
         第一次进来看到这句话的人正是要建第一个库的人，光说「没有」等于把他
         丢在原地；翻到页面右上角才发现那枚按钮不是所有人都想到的。
         与定时任务目录的空态同一写法（那里写「用右上角的『新建任务』创建一个」）。 -->
    <div v-else-if="!loadError && items.length === 0" class="directory-state">
      还没有知识库，用右上角的「创建知识库」新建一个。
    </div>

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
          <td class="status-td">
            <!-- flex 排在里层的 div 上，不能直接写在 <td> 上：td 变成 flex 容器就
                 脱离表格布局，盒子高度由内容决定（开关只有 18px 高，这一格合计约 59px），
                 而不是行高（约 90px）；它那条 border-bottom 于是高出三十来像素，
                 正好切在行中间，整行的分隔线在这一列明显断开错位。
                 单元格自己保持 table-cell，垂直居中交给 td 的 vertical-align。 -->
            <div class="status-cell">
              <!-- 开关与账号目录、任务目录同一个 BaseSwitch：这里原来是个手写的
                   role="switch" checkbox，三个页面三种长相。 -->
              <BaseSwitch
                :checked="item.is_active"
                :label="`启用知识库 ${item.name}`"
                :disabled="saving"
                @change="changeActive($event, item)"
              />
              <span class="status-text" :class="{ 'is-inactive': !item.is_active }">
                {{ item.is_active ? '已启用' : '已停用' }}
              </span>
            </div>
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
/* 目录工具栏（.directory-toolbar / .toolbar-actions / .directory-count 及其图标、
   数字，含窄容器换行）归共享层 directory.css——与来源管理那份逐字相同。 */
/* 内联表单的标题与页脚与工具栏同属「一行 flex」，但只出现在这个表单里，
   不进共享层。 */
.editor-heading,
.editor-footer {
  display: flex;
  align-items: center;
  gap: 12px;
}
.knowledge-editor {
  /* 内联表单不再铺满整行：一是这么宽的表单字段会拉成一条线，二是头部那枚关闭键
     会离标题上千像素，用户接不上「它关的是这个表单」。 */
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
.editor-footer .check-control {
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
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
  padding: 16px 0;
}
.feedback svg {
  flex: 0 0 auto;
}
/* .directory-state 归共享层：styles/components/directory.css（四个目录页同一套）。 */
.knowledge-table {
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

/* 行悬停：账号目录与任务目录两行都有，这两张表漏了。四列表摊开在 1400px 上，
   没有行底就等于没有横向参考线，从「新闻」扫到右边的开关要自己数格子。
   行本身不可点（开关和编辑键才是入口），所以只用底色，不做指针。 */
.knowledge-table tbody tr {
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.knowledge-table tbody tr:hover {
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
  font-size: var(--fs-xs);
  line-height: 1.65;
  white-space: pre-wrap;
}
.key-cell code {
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}
/* 状态列：开关归 BaseSwitch（外观、拨回父值的逻辑、触屏放大都在那里），
   这里只排「开关 + 状态文字」这一行。文字是给读屏和扫视用的明示，不是第二套控件。 */
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
  .status-td {
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
