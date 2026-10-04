<script setup lang="ts">
import { nextTick, onMounted, useTemplateRef, watch } from 'vue'
import { Send, Settings2, Square } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import ComposerDock from '@/shared/ui/ComposerDock.vue'
import { useComposerInput } from '@/shared/composables/useComposerInput'
import { MAX_MESSAGE_CHARACTERS } from '../model/agent-validation'

/* 贴在页面底部的输入区。
 *
 * 视觉外壳是共享的 ComposerDock（与检索输入条同一颗坞）；本组件只管输入行为：
 * 随内容长高、Enter 发送（输入法组合期间不发送）、流式中换成停止键。
 * 字段标签改成 sr-only 保留给读屏——底部就一个输入框，再给它加标题是重复。
 *
 * 自定义系统提示词不在这里：它是「改变模型行为」的配置，不是一条消息，归设置中心的
 * 「Agent 偏好」分区（可发现、可持久、可恢复默认）。输入条只在覆盖生效时亮一枚徽章，
 * 点它直达设置页——状态可见，编辑归位。
 */

const props = defineProps<{
  modelValue: string
  /** 账号偏好里配了非空提示词时为真：输入条亮出徽章，提示「新会话会用它」。 */
  customPromptActive: boolean
  inputError: string | null
  remainingCharacters: number
  streaming: boolean
  /** 服务端手上有一次可停的运行（流式连接或等待态）；为真时底栏显示停止键。 */
  stoppable: boolean
  canSend: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  cancel: []
}>()

/* 草稿转发、Enter 守卫、焦点归还、字数档位与检索输入条共用（见
   shared/composables/useComposerInput.ts）。这里只留 Agent 独有的行为：随内容长高。 */
const inputRef = useTemplateRef<HTMLTextAreaElement>('agent-textarea')
const { draft, tone, onEnter, focusInput } = useComposerInput({
  inputRef,
  value: () => props.modelValue,
  onChange: (value) => emit('update:modelValue', value),
  canSubmit: () => props.canSend,
  submit: () => emit('submit'),
  remainingCharacters: () => props.remainingCharacters,
})

/* 输入框随内容长高，到 CSS 的 max-height（40vh）后转为框内滚动。
   固定 62px 时多行文字在框里滚动，被切半的最后一行紧贴无边框底边，
   视觉上和下面的控件行糊在一起（2026-09 审查的「自定义 Prompt 重叠」）。 */
function autoGrow(): void {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

watch(draft, () => nextTick(autoGrow))
onMounted(autoGrow)

defineExpose({ focusInput })
</script>

<template>
  <!-- form 包住整颗坞：发送键住在坞的底栏插槽里，type=submit 要求它在表单内。 -->
  <form class="agent-form" :aria-busy="streaming" @submit.prevent="emit('submit')">
    <ComposerDock class="agent-composer" label="向 Agent 提问">
      <label class="sr-only" for="agent-message">这一轮的问题</label>
      <textarea
        id="agent-message"
        ref="agent-textarea"
        v-model="draft"
        class="message-input"
        name="message"
        rows="2"
        :maxlength="MAX_MESSAGE_CHARACTERS"
        placeholder="问点什么，Agent 会自己去查。Enter 发送，Shift + Enter 换行"
        :aria-invalid="Boolean(inputError)"
        :aria-describedby="inputError ? 'agent-message-error' : 'agent-message-count'"
        @input="autoGrow"
        @keydown.enter="onEnter"
      ></textarea>

      <p v-if="inputError" id="agent-message-error" class="field-error" role="alert">
        {{ inputError }}
      </p>

      <template #bar-left>
        <!-- 模型选择器插槽：先模型后范围，与「这一轮用谁、看哪些库」的阅读顺序一致。 -->
        <slot name="model" />

        <!-- 知识库范围选择器插槽（与 SearchComposer 范式对齐） -->
        <slot name="scope" />

        <!-- 账号配了自定义提示词时亮徽章，点它直达设置页。默认状态不打扰：没有可调的东西
             就不该占一格。徽章说的是「新会话会用它」——提示词按会话快照，已经开始的会话
             不受设置页改动影响（见 ADR 0029）。 -->
        <RouterLink
          v-if="customPromptActive"
          class="badge-link"
          :to="{ name: 'settings', params: { section: 'agent' } }"
          aria-label="自定义提示词已启用，新会话会使用它；去设置页调整"
          title="自定义提示词已启用，新会话会使用它；去设置页调整"
        >
          <span class="prompt-trigger">
            <Settings2 :size="15" aria-hidden="true" />
            <span class="prompt-badge" aria-hidden="true"></span>
          </span>
          <span class="prompt-badge-text">自定义提示词</span>
        </RouterLink>
        <!-- 「新会话」不在这里：它是整页的主操作，唯一的一枚在外壳侧栏顶部。 -->
      </template>

      <template #bar-right>
        <span id="agent-message-count" class="character-count" :class="tone">
          还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
        </span>

        <BaseButton
          v-if="stoppable"
          class="stop-button"
          variant="danger"
          size="sm"
          type="button"
          aria-label="停止生成"
          title="停止生成"
          @click="emit('cancel')"
        >
          <template #icon><Square :size="15" aria-hidden="true" /></template>
          <span class="sr-only">停止生成</span>
        </BaseButton>
        <!-- 这个分支是 v-if="stoppable" 的 v-else：服务端没有可停的运行时就只能发送。 -->
        <BaseButton
          v-else
          class="send-button"
          variant="primary"
          size="sm"
          type="submit"
          aria-label="发送消息"
          title="发送消息"
          :disabled="!canSend"
        >
          <template #icon><Send :size="16" stroke-width="2.3" aria-hidden="true" /></template>
          <span class="sr-only">发送</span>
        </BaseButton>
      </template>
    </ComposerDock>
  </form>
</template>

<style scoped>
/* 字数胶囊的皮肤（三档色、等宽字）归共享层 character-count.css。这里只留本页的策略：
   常态档失焦时藏起来（纯参考信息，--text-tertiary 在那个字号上对比度不足），
   临近与超出两档自带语气色，无论有没有焦点都要看得见。 */
.agent-composer:not(:focus-within) .character-count {
  visibility: hidden;
}

.agent-composer:not(:focus-within) .character-count.is-near,
.agent-composer:not(:focus-within) .character-count.is-over {
  visibility: visible;
}

.message-input {
  display: block;
  width: 100%;
  max-height: 40vh;
  /* 竖向可拉，但不给横向：横向拉宽会把底部控件行挤出圆角框。 */
  resize: vertical;
  min-height: 62px;
  padding: 6px 0;
  border: 0;
  outline: none;
  color: var(--text-primary);
  background: none;
  font-size: var(--fs-base);
  line-height: 1.65;
}

.message-input:focus-visible {
  box-shadow: none;
}

.message-input::placeholder {
  color: var(--text-tertiary);
}

/* 徽章皮肤（浅强调底胶囊、悬停、按下、触屏高度）归共享层 badge-link.css，
   与检索输入条的「检索偏好」同一份——两处原本各写一遍，高度差了 10px。
   这里只留这一小段特有的结构：图标右上角那枚「已启用」小圆点。 */
.prompt-trigger {
  position: relative;
  display: inline-flex;
}

.prompt-badge {
  position: absolute;
  top: -2px;
  right: -4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  pointer-events: none;
}

.field-error {
  margin-top: 6px;
  color: var(--danger);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

/* 圆形发送键与停止键：与检索页同源规范（38px 圆形纯图标键，移动端触屏 44px 兜底）。 */
.send-button,
.stop-button {
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
  .send-button,
  .stop-button {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}

@container (max-width: 520px) {
  /* 窄屏把字数计数撤掉：它和发送键抢同一行，而上界是 4000 字，
     手机上打到临近值的可能极低。临近/超出两档仍然由语气色显示。 */
  .character-count:not(.is-near):not(.is-over) {
    display: none;
  }

  /* 徽章收成纯图标：文字挤占输入行，图标 + 小圆点已足够表达状态。 */
  .prompt-badge-text {
    display: none;
  }
}
</style>
