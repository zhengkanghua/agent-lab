<script setup lang="ts">
import { computed } from 'vue'
import { BookOpenText, Eraser, Layers3, ListFilter, LoaderCircle, Search } from '@lucide/vue'
import type { SearchMode } from '../model/search-result'

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

      <label class="field-label" for="search-query">研究内容</label>
      <textarea
        id="search-query"
        v-model="query"
        class="query-input"
        name="query"
        rows="6"
        maxlength="4096"
        placeholder="例如：央行近期是否调整利率？"
        :aria-invalid="Boolean(inputError)"
        :aria-describedby="inputError ? 'query-error' : 'query-count'"
      ></textarea>

      <div class="query-meta">
        <span id="query-count" class="character-count" :class="counterTone">
          还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
        </span>
      </div>

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

        <details v-if="mode === 'document'" class="advanced-options">
          <summary>
            <span class="control-label">
              <Layers3 :size="16" aria-hidden="true" />
              每篇相关片段
            </span>
            <b>{{ matchesPerDocument }} 条</b>
          </summary>
          <label>
            <span>最多保留</span>
            <select v-model.number="matchesPerDocument" aria-label="每篇新闻最多显示的相关片段数">
              <option :value="1">1 条</option>
              <option :value="3">3 条</option>
              <option :value="5">5 条</option>
            </select>
          </label>
        </details>

        <div class="composer-actions">
          <button
            v-if="query"
            class="clear-button"
            type="button"
            aria-label="清空检索内容"
            title="清空检索内容"
            @click="emit('clear')"
          >
            <Eraser :size="18" aria-hidden="true" />
          </button>
          <button class="search-button" type="submit" :disabled="loading">
            <LoaderCircle v-if="loading" class="spin" :size="18" aria-hidden="true" />
            <Search v-else :size="18" stroke-width="2.4" aria-hidden="true" />
            <span>
              {{ loading ? '正在搜索' : mode === 'document' ? '搜索新闻' : '搜索片段' }}
            </span>
          </button>
        </div>
      </div>
    </form>

    <p v-if="inputError" id="query-error" class="field-error" role="alert">
      {{ inputError }}
    </p>
  </section>
</template>

<style scoped>
.composer {
  padding: 22px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-md);
  background: var(--paper-50);
  box-shadow: var(--shadow-soft);
}

.composer-heading {
  margin-bottom: 18px;
}

.composer-heading p {
  color: var(--signal-600);
  font-size: 0.72rem;
  font-weight: 760;
  letter-spacing: 0;
}

.composer-heading h2 {
  margin-top: 4px;
  color: var(--ink-950);
  font-family: var(--display-font);
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
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  background: var(--paper-100);
}

.mode-switch button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  border: 0;
  border-radius: 4px;
  color: var(--ink-700);
  background: transparent;
  font-size: 0.78rem;
  font-weight: 720;
  transition:
    color 150ms ease,
    background-color 150ms ease,
    box-shadow 150ms ease;
}

.mode-switch button:hover {
  color: var(--ink-950);
}

.mode-switch button.is-active {
  color: var(--signal-600);
  background: var(--paper-50);
  box-shadow: 0 1px 5px rgba(24, 33, 31, 0.09);
}

.mode-description {
  min-height: 35px;
  padding: 7px 2px 10px;
  color: var(--ink-500);
  font-size: 0.7rem;
  line-height: 1.45;
}

.field-label {
  margin-bottom: 7px;
  color: var(--ink-800);
  font-size: 0.8rem;
  font-weight: 700;
}

.query-input {
  display: block;
  width: 100%;
  min-height: 164px;
  resize: vertical;
  padding: 14px 15px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--ink-950);
  background: #fbfcfb;
  font-size: 0.94rem;
  line-height: 1.65;
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease,
    background-color 150ms ease;
}

.query-input::placeholder {
  color: var(--ink-500);
}

.query-input:hover {
  border-color: #bac4c0;
}

.query-input:focus {
  border-color: var(--source-500);
  background: var(--paper-50);
  box-shadow: 0 0 0 4px var(--source-100);
}

.query-meta {
  display: flex;
  justify-content: flex-end;
  min-height: 30px;
  padding-top: 7px;
}

.character-count {
  color: var(--ink-500);
  font-family: var(--mono-font);
  font-size: 0.67rem;
  letter-spacing: 0;
}

.character-count.is-near {
  color: var(--warning-600);
}

.character-count.is-over {
  color: var(--danger-600);
}

.composer-toolbar {
  display: grid;
  gap: 13px;
  margin-top: 4px;
  padding-top: 16px;
  border-top: 1px solid var(--paper-200);
}

.result-limit-control {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  color: var(--ink-700);
  font-size: 0.8rem;
}

.control-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.control-label svg {
  color: var(--source-600);
}

.result-limit-control select,
.advanced-options select {
  width: 92px;
  height: 38px;
  padding: 0 9px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  outline: 0;
  color: var(--ink-950);
  background: var(--paper-50);
  font-weight: 700;
}

.advanced-options {
  border-top: 1px solid var(--paper-200);
  border-bottom: 1px solid var(--paper-200);
  color: var(--ink-700);
  font-size: 0.8rem;
}

.advanced-options summary,
.advanced-options label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
}

.advanced-options summary {
  cursor: pointer;
  list-style-position: outside;
}

.advanced-options summary b {
  color: var(--ink-800);
  font-size: 0.75rem;
}

.advanced-options label {
  padding: 6px 0 12px 22px;
}

.composer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 与 UserAdminPage 的 .icon-button 无关：那里是 34px 透明幽灵按钮，这里是 44px 实底
   填充按钮，与同行的 .search-button 共享基础几何。曾同名但实现完全不同，已改名区分。 */
.clear-button,
.search-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 44px;
  border: 0;
  border-radius: var(--radius-sm);
  transition:
    background-color 150ms ease,
    color 150ms ease,
    transform 150ms ease;
}

.clear-button {
  flex: 0 0 44px;
  color: var(--ink-700);
  background: var(--paper-200);
}

.clear-button:hover {
  color: var(--ink-950);
  background: var(--paper-300);
}

.search-button {
  flex: 1;
  gap: 8px;
  padding: 0 16px;
  color: var(--paper-50);
  background: var(--signal-500);
  font-weight: 760;
}

.search-button:hover:not(:disabled) {
  background: var(--signal-600);
  transform: translateY(-1px);
}

.search-button:disabled {
  cursor: wait;
  opacity: 0.72;
}

.field-error {
  margin-top: 12px;
  color: var(--danger-600);
  font-size: 0.8rem;
  font-weight: 650;
}

/* .spin 见 styles/components/motion.css。 */

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
