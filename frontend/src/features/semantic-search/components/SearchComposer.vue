<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search, SlidersHorizontal } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import ComposerDock from '@/shared/ui/ComposerDock.vue'
import { MAX_QUERY_CHARACTERS } from '../model/search-validation'

/* 检索页顶部常驻的输入条（Q3 / Q4 模型二）。
 *
 * 视觉外壳是共享的 ComposerDock（与 Agent 输入条同一颗坞）；本组件只管输入行为：
 * Enter 提交（输入法组合期间不提交）、字数上界、提交后把焦点还给输入框。
 *
 * 数量参数不在这里：它们是全局默认、影响之后所有检索的偏好，归设置中心的「检索偏好」
 * 分区（可发现、可持久）；输入条只留一个跳转入口（底栏滑杆图标），悬停能看到当前值。
 * 知识库范围选择器由页面经 #scope 插槽放进底栏左侧：组件不管它的数据。
 */

const textareaRef = ref<HTMLTextAreaElement | null>(null)

const props = withDefaults(
  defineProps<{
    modelValue: string
    loading: boolean
    disabled?: boolean
    inputError: string | null
    remainingCharacters: number
    /** 悬停在设置入口上时展示的当前参数摘要，如「每次 10 篇 · 每篇 3 条」。 */
    preferenceSummary?: string
  }>(),
  { preferenceSummary: undefined },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
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
  if (!props.loading && !props.disabled) emit('submit')
}

/** 让父级把焦点放回输入框（Q11：提交一轮后清空草稿、焦点留下，方便连续换词）。 */
function focusInput(): void {
  textareaRef.value?.focus()
}

defineExpose({ focusInput })
</script>

<template>
  <!-- form 包住整颗坞：发送键住在坞的底栏插槽里，type=submit 要求它在表单内。 -->
  <form class="search-form" :aria-busy="loading" @submit.prevent="emit('submit')">
    <ComposerDock label="语义检索输入条">
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
            placeholder="搜索文档中的问题或主题..."
            @keydown.enter="onEnter"
          ></textarea>
        </template>
      </BaseField>

      <template #bar-left>
        <!-- 知识库范围选择器由页面塞进来（数据与刷新归页面管），这里只留位。 -->
        <slot name="scope" />

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
      </template>

      <template #bar-right>
        <span class="character-count" :class="counterTone" aria-hidden="true">
          {{ remainingCharacters.toLocaleString('zh-CN') }}
        </span>

        <BaseButton
          class="search-submit"
          variant="primary"
          size="sm"
          type="submit"
          aria-label="搜索文档"
          title="搜索文档"
          :loading="loading"
          :disabled="disabled || (!draft.trim() && !loading)"
        >
          <template #icon><Search :size="16" stroke-width="2.4" aria-hidden="true" /></template>
        </BaseButton>
      </template>
    </ComposerDock>
  </form>
</template>

<style scoped>
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
  font-size: var(--fs-lg);
  line-height: 1.5;
  font-family: inherit;
}

.query-input::placeholder {
  color: var(--text-tertiary);
  font-weight: var(--fw-normal);
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

/* 校验错误用 BaseField 的普通流内提示：在坞内展开，把底栏自然推下去。 */

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

/* 触屏下撑到 44px。38px 是与 BaseIconButton 的 md 对齐的视觉尺寸，但这是链接、
   不复用那个组件（要中键新开、读屏报「链接」），因此拿不到那边的触屏规则，
   只能在这里自己补一条。 */
@media (pointer: coarse) {
  .prefs-link {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}

.character-count {
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  padding-right: 4px;
}

.character-count.is-near {
  color: var(--warning);
}

.character-count.is-over {
  color: var(--danger);
}

/* 圆形发送键：有字才实色（primary 的 disabled 态），空时灰。 */
.search-submit {
  width: 38px;
  height: 38px;
  min-width: 38px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}

@media (pointer: coarse) {
  .search-submit {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}

@container (max-width: 600px) {
  .character-count {
    display: none;
  }
}
</style>
