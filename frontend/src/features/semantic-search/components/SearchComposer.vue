<script setup lang="ts">
import { computed, useTemplateRef } from 'vue'
import { Search, SlidersHorizontal } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import ComposerDock from '@/shared/ui/ComposerDock.vue'
import { useComposerInput } from '@/shared/composables/useComposerInput'
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

const props = withDefaults(
  defineProps<{
    modelValue: string
    loading: boolean
    disabled?: boolean
    inputError: string | null
    remainingCharacters: number
    /** 检索偏好入口上展示的当前参数摘要，如「每次 10 篇 · 每篇 3 条」。 */
    preferenceSummary?: string
  }>(),
  { preferenceSummary: undefined },
)

/** 入口的无障碍名：可见文字在窄容器里会被收起，这个名字始终带着当前值。 */
const prefsLabel = computed(() =>
  props.preferenceSummary ? `检索偏好设置：${props.preferenceSummary}` : '检索偏好设置',
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
}>()

/* 草稿转发、Enter 守卫、焦点归还、字数档位与 Agent 输入条共用：
   见 shared/composables/useComposerInput.ts。两条输入条只有提交条件不同。 */
const inputRef = useTemplateRef<HTMLTextAreaElement>('query-textarea')
const { draft, tone, onEnter, focusInput } = useComposerInput({
  inputRef,
  value: () => props.modelValue,
  onChange: (value) => emit('update:modelValue', value),
  canSubmit: () => !props.loading && !props.disabled,
  submit: () => emit('submit'),
  remainingCharacters: () => props.remainingCharacters,
})

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
            ref="query-textarea"
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
        <!-- 字数走 BaseField 的 hint：它把 aria-describedby 接到这里，读屏才听得到。
             底栏右侧那份是给眼睛看的（aria-hidden），两者文案同源、与 Agent 输入条一致。 -->
        <template #hint>
          还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
        </template>
      </BaseField>

      <template #bar-left>
        <!-- 知识库范围选择器由页面塞进来（数据与刷新归页面管），这里只留位。 -->
        <slot name="scope" />

        <!-- 数量参数的入口迁去了设置中心；这里留一枚带当前值的胶囊，形态与 Agent
             输入条的自定义提示词徽章一致。原来是一枚无标签的滑杆图标：不悬停就
             没人知道它通向哪里，而「在哪里调参数」不该是需要翻文档才知道的事。
             窄容器里只收起可见文字，aria-label 仍带着当前值，读屏不丢信息。 -->
        <RouterLink
          class="badge-link"
          :to="{ name: 'settings', params: { section: 'search' } }"
          :aria-label="prefsLabel"
          :title="prefsLabel"
        >
          <SlidersHorizontal :size="15" aria-hidden="true" />
          <span class="prefs-label" aria-hidden="true">{{ preferenceSummary ?? '检索偏好' }}</span>
        </RouterLink>
      </template>

      <template #bar-right>
        <span class="character-count" :class="tone" aria-hidden="true">
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

/* 设置入口的皮肤（浅强调底胶囊、悬停、按下、触屏高度）归共享层 badge-link.css，
   与 Agent 输入条的自定义提示词徽章共用一份。这里只留一件事：它为什么是链接而不是
   BaseIconButton——要中键新开、要读屏报「链接」。 */

/* 字数胶囊的皮肤（三档色、等宽字）归共享层 character-count.css。 */
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

/* 常态档不显示。底栏右侧孤零零一个「4,096」既没说单位也没说是上限，
   读起来更像一串编号；而检索词上界 4096 字，真实提问短得多，这个数字
   在正常使用中永远逼近不到，属于纯噪音。临近与超出两档自带提醒色，
   那时它才有意义，也才该出现。
   原来的「600px 以下整个隐藏」被这一条覆盖，去掉：手机上「快超了」同样要看得到。
   读屏不受影响：完整句子仍在 BaseField 的 hint 里（那条被隐藏的 aria-describedby）。 */
.character-count:not(.is-near):not(.is-over) {
  display: none;
}

/* 窄容器里只留图标：胶囊文字与范围选择器在同一行抢位置。
   收起的只是装饰性的可见文字，入口的无障碍名在 aria-label 上，不受影响。 */
@container (max-width: 520px) {
  .prefs-label {
    display: none;
  }
}
</style>
