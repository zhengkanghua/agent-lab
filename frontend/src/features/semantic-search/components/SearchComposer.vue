<script setup lang="ts">
import { computed } from 'vue'
import { BookOpenText, Eraser, Layers3, ListFilter, Search } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseDisclosure from '@/shared/ui/BaseDisclosure.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import type { SearchMode } from '../model/search-result'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

const props = defineProps<{
  mode: SearchMode
  modelValue: string
  documentLimit: number
  topK: number
  matchesPerDocument: number
  loading: boolean
  inputError: string | null
  remainingCharacters: number
}>()

const emit = defineEmits<{
  'update:mode': [value: SearchMode]
  'update:modelValue': [value: string]
  'update:documentLimit': [value: number]
  'update:topK': [value: number]
  'update:matchesPerDocument': [value: number]
  submit: []
  clear: []
}>()

const query = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

const documentLimit = computed({
  get: () => props.documentLimit,
  set: (value: number) => emit('update:documentLimit', value),
})

const topK = computed({
  get: () => props.topK,
  set: (value: number) => emit('update:topK', value),
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
</script>

<template>
  <section class="composer" aria-labelledby="composer-title">
    <div class="composer-heading">
      <p>检索条件</p>
      <h2 id="composer-title">输入问题或主题</h2>
    </div>

    <form class="search-form" :aria-busy="loading" @submit.prevent="emit('submit')">
      <div class="mode-switch" role="group" aria-label="搜索结果模式">
        <button
          type="button"
          :class="{ 'is-active': mode === 'document' }"
          :aria-pressed="mode === 'document'"
          @click="emit('update:mode', 'document')"
        >
          <BookOpenText :size="15" aria-hidden="true" />
          按新闻
        </button>
        <button
          type="button"
          :class="{ 'is-active': mode === 'chunk' }"
          :aria-pressed="mode === 'chunk'"
          @click="emit('update:mode', 'chunk')"
        >
          <Layers3 :size="15" aria-hidden="true" />
          按片段
        </button>
      </div>
      <p class="mode-description">
        {{
          mode === 'document'
            ? '同一新闻的相关片段合并展示。'
            : '逐条保留向量检索返回的原始 Chunk。'
        }}
      </p>

      <BaseField id="search-query" label="研究内容" :error="inputError ?? undefined">
        <template #default="{ control }">
          <textarea
            v-bind="control"
            v-model="query"
            class="query-input"
            name="query"
            rows="6"
            :maxlength="MAX_QUERY_CHARACTERS"
            placeholder="例如：央行近期是否调整利率？"
          ></textarea>
        </template>
        <template #hint>
          <span class="character-count" :class="counterTone">
            还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
          </span>
        </template>
      </BaseField>

      <div class="composer-toolbar">
        <label class="result-limit-control">
          <span class="control-label">
            <ListFilter :size="16" aria-hidden="true" />
            {{ mode === 'document' ? '文章数量' : '片段数量' }}
          </span>
          <select
            v-if="mode === 'document'"
            v-model.number="documentLimit"
            aria-label="最多显示的新闻数量"
          >
            <option :value="1">1 篇</option>
            <option :value="5">5 篇</option>
            <option :value="10">10 篇</option>
            <option :value="20">20 篇</option>
          </select>
          <select v-else v-model.number="topK" aria-label="最多显示的原始片段数量">
            <option :value="1">1 条</option>
            <option :value="5">5 条</option>
            <option :value="10">10 条</option>
            <option :value="20">20 条</option>
          </select>
        </label>

        <BaseDisclosure
          v-if="mode === 'document'"
          class="advanced-options"
          summary="每篇相关片段"
          :meta="`${matchesPerDocument} 条`"
          tone="plain"
        >
          <template #icon><Layers3 :size="16" aria-hidden="true" /></template>
          <label>
            <span>最多保留</span>
            <select v-model.number="matchesPerDocument" aria-label="每篇新闻最多显示的相关片段数">
              <option :value="1">1 条</option>
              <option :value="3">3 条</option>
              <option :value="5">5 条</option>
            </select>
          </label>
        </BaseDisclosure>

        <div class="composer-actions">
          <BaseButton
            v-if="query"
            class="clear-button"
            variant="secondary"
            icon-only
            aria-label="清空检索内容"
            title="清空检索内容"
            @click="emit('clear')"
          >
            <Eraser :size="18" aria-hidden="true" />
          </BaseButton>
          <BaseButton variant="primary" block type="submit" :loading="loading">
            <template #icon>
              <Search :size="18" stroke-width="2.4" aria-hidden="true" />
            </template>
            <span>
              {{ loading ? '正在搜索' : mode === 'document' ? '搜索新闻' : '搜索片段' }}
            </span>
          </BaseButton>
        </div>
      </div>
    </form>
  </section>
</template>

<style scoped>
.composer {
  padding: 22px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
}

.composer-heading {
  margin-bottom: 18px;
}

.composer-heading p {
  color: var(--text-secondary);
  font-size: 0.72rem;
  font-weight: 760;
  letter-spacing: 0;
}

.composer-heading h2 {
  margin-top: 4px;
  color: var(--text-primary);
  font-size: 1.28rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.3;
}

.search-form {
  display: grid;
}

.mode-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--surface-base);
}

.mode-switch button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  border: 0;
  border-radius: 4px;
  color: var(--text-secondary);
  background: transparent;
  font-size: 0.78rem;
  font-weight: 720;
  transition:
    color 150ms ease,
    background-color 150ms ease,
    box-shadow 150ms ease;
}

.mode-switch button:hover {
  color: var(--text-primary);
}

.mode-switch button.is-active {
  color: var(--accent);
  background: var(--surface-raised);
  box-shadow: var(--shadow-raised);
}

.mode-description {
  min-height: 35px;
  padding: 7px 2px 10px;
  color: var(--text-muted);
  font-size: 0.7rem;
  line-height: 1.45;
}

/* 标签、错误、说明位与 aria 接线归 BaseField。输入框本身留在这里：
   它的高度、可拖拽调整、聚焦态都是本页专有的。 */
.query-input {
  display: block;
  width: 100%;
  min-height: 164px;
  resize: vertical;
  padding: 14px 15px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-base);
  font-size: 0.94rem;
  line-height: 1.65;
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease,
    background-color 150ms ease;
}

.query-input::placeholder {
  color: var(--text-muted);
}

.query-input:hover {
  border-color: var(--border-strong);
}

.query-input:focus {
  border-color: var(--accent);
  background: var(--surface-raised);
  box-shadow: 0 0 0 4px var(--accent-soft);
}

/* 字数落在 BaseField 的 hint 位（一个左对齐的 <p>），这里把它自己撑满再右对齐，
   不去改 BaseField 的 .field-hint——那是别的字段也在用的公共样式。 */
.character-count {
  display: block;
  text-align: right;
  color: var(--text-muted);
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
  display: grid;
  gap: 13px;
  margin-top: 4px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-sunken);
}

.result-limit-control {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  color: var(--text-secondary);
  font-size: 0.8rem;
}

.control-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.control-label svg {
  color: var(--accent);
}

.result-limit-control select,
.advanced-options select {
  width: 92px;
  height: 38px;
  padding: 0 9px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: 0;
  color: var(--text-primary);
  background: var(--surface-raised);
  font-weight: 700;
}

.advanced-options {
  border-top: 1px solid var(--surface-sunken);
  border-bottom: 1px solid var(--surface-sunken);
  color: var(--text-secondary);
  font-size: 0.8rem;
}

/* 摘要行的排布、箭头、meta 值都归 BaseDisclosure。这里只补一条本行专有的高度：
   要和上面 .result-limit-control 的 44px 对齐，两行看起来才是一组。 */
.advanced-options :deep(.disclosure-summary) {
  min-height: 44px;
}

.advanced-options label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  padding: 6px 0 12px 22px;
}

.composer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 两个按钮的几何、配色、悬停与加载态都归 BaseButton（清空 = secondary + icon-only，
   搜索 = primary + block）。只剩清空按钮不参与伸缩这一条：它是固定宽的方块。 */
.clear-button {
  flex: 0 0 44px;
}

@media (max-width: 980px) and (min-width: 641px) {
  .composer-toolbar {
    grid-template-columns: minmax(190px, 0.7fr) minmax(240px, 1fr);
    align-items: end;
  }
}

@media (max-width: 420px) {
  .composer {
    padding: 18px 15px;
  }

  .query-input {
    min-height: 142px;
  }
}
</style>
