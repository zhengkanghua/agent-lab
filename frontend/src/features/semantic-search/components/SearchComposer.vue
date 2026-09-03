<script setup lang="ts">
import { computed, ref } from 'vue'
import { Eraser, Layers3, ListFilter, Search } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BasePopover from '@/shared/ui/BasePopover.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

/* 检索页顶部常驻的输入条（Q3 / Q4 模型二）。
 *
 * 去掉了「按片段」模式切换和 idle/搜后两态切换：这一条固定在页面顶部、始终同样形态，
 * 输入在顶、最新检索记录顶在其正下方，视觉上构成一条连续向下的检索流。数量参数是全局
 * 一份（Q7 甲）影响之后所有轮；「每篇相关片段」是次要设置，收进折叠区不占主行。
 */

const textareaRef = ref<HTMLTextAreaElement | null>(null)

const props = withDefaults(
  defineProps<{
    modelValue: string
    documentLimit: number
    matchesPerDocument: number
    loading: boolean
    inputError: string | null
    remainingCharacters: number
    /** 是否已有一条以上检索记录：决定「清空检索流」要不要出现。 */
    hasRecords: boolean
  }>(),
  {},
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:documentLimit': [value: number]
  'update:matchesPerDocument': [value: number]
  submit: []
  clear: []
}>()

const draft = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

const documentLimit = computed({
  get: () => props.documentLimit,
  set: (value: number) => emit('update:documentLimit', value),
})

const matchesPerDocument = computed({
  get: () => props.matchesPerDocument,
  set: (value: number) => emit('update:matchesPerDocument', value),
})

const counterTone = computed(() => {
  if (props.remainingCharacters < 0) return 'is-over'
  if (props.remainingCharacters < 200) return 'is-near'
  return ''
})

function onEnter(event: KeyboardEvent): void {
  // 输入法组合期间按 Enter 是「确认候选词」，不能当提交。Shift+Enter 换行。
  if (event.isComposing || event.shiftKey) return
  event.preventDefault()
  if (!props.loading) emit('submit')
}

/** 让父级把焦点放回输入框（Q11：提交一轮后清空草稿、焦点留下，方便连续换词）。 */
function focusInput(): void {
  textareaRef.value?.focus()
}

defineExpose({ focusInput })
</script>

<template>
  <section class="composer" aria-label="语义检索输入条" style="container-type: inline-size">  
    <form class="search-form" :aria-busy="loading" @submit.prevent="emit('submit')">
      <!-- Top configuration row -->
      <div class="composer-toolbar-top">
        <label class="result-limit-control">
          <span class="control-label">
            <ListFilter :size="16" aria-hidden="true" />
            新闻数量
          </span>
          <select
            v-model.number="documentLimit"
            :disabled="loading"
            aria-label="每次检索最多显示的新闻数量（影响之后所有检索）"
          >
            <option :value="1">1 篇</option>
            <option :value="5">5 篇</option>
            <option :value="10">10 篇</option>
            <option :value="20">20 篇</option>
          </select>
        </label>

        <BasePopover
          side="bottom"
          align="start"
        >
          <template #trigger>
            <button
              type="button"
              class="advanced-options-trigger"
              aria-label="更多设置"
            >
              <Layers3 :size="15" aria-hidden="true" />
              <span class="trigger-label">更多设置</span>
              <span class="trigger-meta">每篇 {{ matchesPerDocument }} 条相关片段</span>
            </button>
          </template>
          
          <div class="advanced-options-content">
            <label>
              <span>每篇新闻最多保留的相关片段</span>
              <select
                v-model.number="matchesPerDocument"
                aria-label="每篇新闻最多显示的相关片段数（影响之后所有检索）"
              >
                <option :value="1">1 条</option>
                <option :value="3">3 条</option>
                <option :value="5">5 条</option>
              </select>
            </label>
          </div>
        </BasePopover>
      </div>

      <!-- Unified Input Area -->
      <div class="query-container" :class="{ 'has-error': !!inputError }">
        <BaseField id="search-query" label="研究内容" :error="inputError ?? undefined" class="query-field">
          <template #default="{ control }">
            <textarea
              ref="textareaRef"
              v-bind="control"
              v-model="draft"
              class="query-input"
              name="query"
              rows="1"
              :maxlength="MAX_QUERY_CHARACTERS"
              placeholder="输入一个新闻研究问题或主题..."
              @keydown.enter="onEnter"
            ></textarea>
          </template>
        </BaseField>

        <div class="query-actions">
          <span class="character-count" :class="counterTone" aria-hidden="true">
            {{ remainingCharacters.toLocaleString('zh-CN') }}
          </span>

          <BaseButton
            v-if="hasRecords"
            class="clear-button"
            variant="ghost"
            size="sm"
            aria-label="清空当前检索流"
            title="清空当前检索流"
            :disabled="loading"
            @click="emit('clear')"
          >
            <template #icon><Eraser :size="16" aria-hidden="true" /></template>
          </BaseButton>

          <BaseButton
            class="search-submit"
            variant="primary"
            size="sm"
            type="submit"
            aria-label="搜索新闻"
            title="搜索新闻"
            :loading="loading"
            :disabled="!draft.trim() && !loading"
          >
            <template #icon><Search :size="16" stroke-width="2.4" aria-hidden="true" /></template>
          </BaseButton>
        </div>
      </div>
    </form>
  </section>
</template>

<style scoped>
.composer {
  padding: 14px 16px 16px;
  background: transparent;
  transition: all 150ms ease;
}

.search-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 字段标签与字数说明的接线归 BaseField。输入框留在这里：高度、resize、聚焦态是本页专有的。 */
.query-input {
  display: block;
  width: 100%;
  min-height: 52px;
  resize: vertical;
  padding: 12px 0 8px;
  border: none;
  border-bottom: 2px solid var(--border-subtle);
  border-radius: 0;
  outline: none;
  box-shadow: none;
  color: var(--text-primary);
  background: transparent;
  font-size: 1.15rem;
  line-height: 1.65;
  transition:
    border-color 200ms ease,
    background-color 200ms ease;
}

.query-input::placeholder {
  color: var(--text-tertiary);
  font-weight: 400;
}

.query-input:focus,
.query-input:focus-visible {
  outline: none;
  box-shadow: none;
  border-bottom: 2px solid var(--accent);
  background: transparent;
}

.character-count {
  display: block;
  text-align: right;
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: 0.67rem;
  letter-spacing: 0;
}

.character-count.is-near {
  color: var(--warning);
}

.character-count.is-over {
  color: var(--danger);
}

.composer-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  column-gap: 22px;
  row-gap: 10px;
}

.result-limit-control {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 0.8rem;
  white-space: nowrap;
}

.control-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.control-label svg {
  color: var(--text-tertiary);
}

.result-limit-control select,
.advanced-options-content select {
  width: 88px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: 0;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-weight: 700;
  font-size: 0.8rem;
}

.advanced-options-trigger {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-primary);
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
  transition: color var(--duration-fast) ease;
}

.advanced-options-trigger svg {
  color: var(--accent);
}

.advanced-options-trigger:hover {
  color: var(--accent-hover);
}

.advanced-options-trigger .trigger-meta {
  margin-left: 8px;
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.68rem;
  font-weight: 400;
}

.advanced-options-content label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 0.78rem;
}

/* 底部输入框容器 */
.query-container {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  transition:
    border-color var(--duration-fast) ease,
    box-shadow var(--duration-fast) ease;
}

.query-container:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}

.query-container.has-error {
  border-color: var(--danger);
  box-shadow: 0 0 0 3px var(--danger-soft);
}

.query-field {
  flex: 1;
}

/* 隐藏视觉标签，仅为读屏保留 */
.query-field :deep(.field-label) {
  border: 0;
  clip: rect(0 0 0 0);
  height: 1px;
  margin: -1px;
  overflow: hidden;
  padding: 0;
  position: absolute;
  width: 1px;
}

/* 我们已经在右侧手动放了字数，隐藏 BaseField 默认的 hint */
.query-field :deep(.field-hint) {
  display: none;
}

.query-field :deep(.field-error) {
  position: absolute;
  bottom: -22px;
  left: 0;
  margin: 0;
  font-size: 0.75rem;
}

.query-input {
  display: block;
  width: 100%;
  min-height: 44px;
  max-height: 150px;
  resize: vertical;
  padding: 8px 0;
  border: none;
  outline: none;
  box-shadow: none;
  color: var(--text-primary);
  background: transparent;
  font-size: 1.1rem;
  line-height: 1.5;
  font-family: inherit;
}

.query-input::placeholder {
  color: var(--text-tertiary);
  font-weight: 400;
}

.query-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 4px;
}

.character-count {
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: 0.75rem;
  padding-right: 4px;
}

.character-count.is-near {
  color: var(--warning);
}

.character-count.is-over {
  color: var(--danger);
}

.clear-button {
  color: var(--text-secondary);
  border-radius: var(--radius-lg);
}

.clear-button :deep(svg) {
  color: var(--text-tertiary);
}

.search-submit {
  min-width: 44px; /* Icon button style since text is removed */
  border-radius: var(--radius-lg);
  padding: 0;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
}

@container (max-width: 600px) {
  .composer {
    padding: 12px 12px 10px;
  }

  .query-input {
    min-height: 58px;
  }

  .character-count,
  .advanced-options {
    display: none;
  }
}
</style>
