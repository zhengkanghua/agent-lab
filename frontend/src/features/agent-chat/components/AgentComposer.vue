<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { MessageSquarePlus, Send, Settings2, Square } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { MAX_MESSAGE_CHARACTERS } from '../model/agent-validation'

/* 贴在页面底部的输入区。
 *
 * 形态是一个圆角框：上面是文本域，下面一行控件。它不再是侧栏里的一张卡片，
 * 所以标题「向 Agent 提问」和可见的字段标签都撤掉了——底部就一个输入框，
 * 再给它加标题是重复。字段标签改成 sr-only 保留给读屏。
 *
 * 自定义系统提示词不在这里：它是「改变模型行为」的配置，不是一条消息，归设置中心的
 * 「Agent 偏好」分区（可发现、可持久、可恢复默认）。输入条只在覆盖生效时亮一枚徽章，
 * 点它直达设置页——状态可见，编辑归位。
 */

const props = defineProps<{
  modelValue: string
  /** 偏好里已保存非空提示词时为真：输入条亮出「已启用」徽章。 */
  customPromptActive: boolean
  inputError: string | null
  remainingCharacters: number
  streaming: boolean
  canSend: boolean
  hasHistory: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  cancel: []
  'new-conversation': []
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

const messageInputRef = ref<HTMLTextAreaElement | null>(null)

/* 输入框随内容长高，到 CSS 的 max-height（40vh）后转为框内滚动。
   固定 62px 时多行文字在框里滚动，被切半的最后一行紧贴无边框底边，
   视觉上和下面的控件行糊在一起（2026-09 审查的「自定义 Prompt 重叠」）。 */
function autoGrow(): void {
  const el = messageInputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

watch(draft, () => nextTick(autoGrow))
onMounted(autoGrow)

/**
 * Enter 发送、Shift+Enter 换行。
 *
 * 输入法组合期间不能发送：中文输入按 Enter 是「确认候选词」，此时 isComposing 为真，
 * 不拦住会把半个词发出去。
 */
function onEnter(event: KeyboardEvent): void {
  if (event.isComposing || event.shiftKey) return
  event.preventDefault()
  if (props.canSend) emit('submit')
}
</script>

<template>
  <section class="agent-composer" aria-label="向 Agent 提问" style="container-type: inline-size">
    <form class="agent-form" :aria-busy="streaming" @submit.prevent="emit('submit')">
      <label class="sr-only" for="agent-message">这一轮的问题</label>
      <textarea
        id="agent-message"
        ref="messageInputRef"
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

      <div class="composer-bar">
        <div class="bar-left">
          <!-- 覆盖生效时亮徽章，点它直达设置页。默认状态不打扰：没有可调的东西
               就不该占一格。 -->
          <RouterLink
            v-if="customPromptActive"
            class="prompt-badge-link"
            :to="{ name: 'settings', params: { section: 'agent' } }"
            aria-label="自定义提示词已启用，去设置页调整"
            title="自定义提示词已启用，去设置页调整"
          >
            <span class="prompt-trigger">
              <Settings2 :size="15" aria-hidden="true" />
              <span class="prompt-badge" aria-hidden="true"></span>
            </span>
            <span class="prompt-badge-text">自定义提示词</span>
          </RouterLink>

          <BaseButton
            v-if="hasHistory"
            class="secondary-button"
            variant="ghost"
            size="sm"
            :disabled="streaming"
            @click="emit('new-conversation')"
          >
            <template #icon><MessageSquarePlus :size="16" aria-hidden="true" /></template>
            新会话
          </BaseButton>
        </div>

        <div class="bar-right">
          <span id="agent-message-count" class="character-count" :class="counterTone">
            还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
          </span>

          <BaseButton
            v-if="streaming"
            class="stop-button"
            variant="danger"
            size="sm"
            @click="emit('cancel')"
          >
            <template #icon><Square :size="15" aria-hidden="true" /></template>
            停止生成
          </BaseButton>
          <!-- 这个分支是 v-if="streaming" 的 v-else，streaming 恒为假，
               所以不需要转圈：流式中显示的是上面那个停止键。 -->
          <BaseButton
            v-else
            class="send-button"
            variant="primary"
            size="sm"
            type="submit"
            :disabled="!canSend"
          >
            <template #icon><Send :size="16" stroke-width="2.3" aria-hidden="true" /></template>
            发送
          </BaseButton>
        </div>
      </div>
    </form>

    <p v-if="inputError" id="agent-message-error" class="field-error" role="alert">
      {{ inputError }}
    </p>
  </section>
</template>

<style scoped>
.agent-composer {
  padding: 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    box-shadow var(--duration-fast) var(--ease-out-smooth);
}

/* 焦点环画在外框上而不是文本域上：视觉上这一整块是一个输入控件。
   文本域自身的焦点样式随之去掉，否则会出现两层环。 */
.agent-composer:focus-within {
  border-color: var(--accent);
  box-shadow:
    0 0 0 4px var(--accent-soft),
    var(--shadow-soft);
}

.agent-form {
  display: grid;
}

.message-input {
  display: block;
  width: 100%;
  max-height: 40vh;
  /* 竖向可拉，但不给横向：横向拉宽会把底部控件行挤出圆角框。 */
  resize: vertical;
  min-height: 62px;
  padding: 6px 7px;
  border: 0;
  outline: none;
  color: var(--text-primary);
  background: none;
  font-size: 0.95rem;
  line-height: 1.65;
}

.message-input:focus-visible {
  box-shadow: none;
}

.message-input::placeholder {
  color: var(--text-tertiary);
}

.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 4px;
}

.bar-left,
.bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.prompt-badge-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 4px 9px;
  border: 1px solid var(--accent-soft);
  border-radius: var(--radius-pill);
  color: var(--accent);
  background: var(--accent-soft);
  font-size: 0.72rem;
  font-weight: 700;
  white-space: nowrap;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    border-color var(--duration-fast) var(--ease-out-smooth);
}

.prompt-badge-link:hover {
  color: var(--accent-hover);
  border-color: var(--accent);
}

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

.character-count {
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: 0.67rem;
  white-space: nowrap;
  transition: color var(--duration-normal) var(--ease-out-smooth);
}

/* 只有临近与超出上界时才需要被看见,那两档自带语气色。默认那档是纯参考信息,
   --text-tertiary 在这个字号上对比度不足,所以平时不显示,聚焦时才出现。 */
.agent-composer:not(:focus-within) .character-count {
  visibility: hidden;
}

.character-count.is-near {
  color: var(--warning);
  visibility: visible;
}

.character-count.is-over {
  color: var(--danger);
  visibility: visible;
}

.field-error {
  padding: 7px 7px 2px;
  color: var(--danger);
  font-size: 0.78rem;
  font-weight: 650;
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

  .prompt-badge-link {
    padding: 6px 8px;
  }
}
</style>
