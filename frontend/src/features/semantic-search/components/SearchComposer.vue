<script setup lang="ts">
import { computed, ref } from 'vue'
import { Eraser, Search, SlidersHorizontal } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

/* 检索页顶部常驻的输入条（Q3 / Q4 模型二）。
 *
 * 去掉了「按片段」模式切换和 idle/搜后两态切换：这一条固定在页面顶部、始终同样形态，
 * 输入在顶、最新检索记录顶在其正下方，视觉上构成一条连续向下的检索流。
 *
 * 数量参数不在这里：它们是全局默认、影响之后所有检索的偏好，归设置中心的「检索偏好」
 * 分区（可发现、可持久）；输入条只留一个跳转入口（右下角滑杆图标），悬停能看到当前值。
 */

const textareaRef = ref<HTMLTextAreaElement | null>(null)

const props = withDefaults(
  defineProps<{
    modelValue: string
    loading: boolean
    inputError: string | null
    remainingCharacters: number
    /** 是否已有一条以上检索记录：决定「清空检索流」要不要出现。 */
    hasRecords: boolean
    /** 悬停在设置入口上时展示的当前参数摘要，如「每次 10 篇 · 每篇 3 条」。 */
    preferenceSummary?: string
  }>(),
  { preferenceSummary: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  clear: []
}>()

const draft = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
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
      <div class="query-container" :class="{ 'has-error': !!inputError }">
        <BaseField
          id="search-query"
          label="研究内容"
          :error="inputError ?? undefined"
          class="query-field"
        >
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

          <!-- 数量参数的入口迁去了设置中心；这里保留一个能直达的图标，
               不让「在哪里调参数」变成需要翻文档才知道的事。 -->
          <RouterLink
            class="prefs-link"
            :to="{ name: 'settings', params: { section: 'search' } }"
            aria-label="检索偏好设置"
            :title="preferenceSummary ?? '检索偏好设置'"
          >
            <SlidersHorizontal :size="16" aria-hidden="true" />
          </RouterLink>

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
  padding: 0;
}

.search-form {
  display: flex;
  flex-direction: column;
}

/* 字段标签与字数说明的接线归 BaseField。输入框留在这里：高度、resize、聚焦态是本页专有的。 */
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

/* 设置入口与 BaseIconButton 的视觉一档对齐（同尺寸、同悬停），
   但它是链接——要中键新开、要读屏报「链接」。 */
.prefs-link {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.prefs-link:hover {
  border-color: var(--border-subtle);
  color: var(--accent);
  background: var(--surface-base);
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
  .character-count {
    display: none;
  }
}
</style>
