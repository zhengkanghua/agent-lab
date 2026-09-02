<script setup lang="ts">
import { computed, ref } from 'vue'
import { MessageSquarePlus, Send, Settings2, Square } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import BasePopover from '@/shared/ui/BasePopover.vue'
import { MAX_MESSAGE_CHARACTERS, MAX_SYSTEM_PROMPT_CHARACTERS } from '../model/agent-validation'

/* 贴在页面底部的输入区。
 *
 * 形态是一个圆角框：上面是文本域，下面一行控件。它不再是侧栏里的一张卡片，
 * 所以标题「向 Agent 提问」和可见的字段标签都撤掉了——底部就一个输入框，
 * 再给它加标题是重复。字段标签改成 sr-only 保留给读屏。
 */

const props = defineProps<{
  modelValue: string
  systemPrompt: string
  defaultPrompt: string | null
  inputError: string | null
  remainingCharacters: number
  streaming: boolean
  canSend: boolean
  hasHistory: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:systemPrompt': [value: string]
  submit: []
  cancel: []
  'new-conversation': []
}>()

const promptOpen = ref(false)

const draft = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

const promptDraft = computed({
  get: () => props.systemPrompt,
  set: (value: string) => emit('update:systemPrompt', value),
})

const counterTone = computed(() => {
  if (props.remainingCharacters < 0) return 'is-over'
  if (props.remainingCharacters < 200) return 'is-near'
  return ''
})

const promptOverridden = computed(() => props.systemPrompt.trim().length > 0)

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

function useDefaultPrompt(): void {
  if (props.defaultPrompt !== null) emit('update:systemPrompt', props.defaultPrompt)
}
</script>

<template>
  <section class="agent-composer" aria-label="向 Agent 提问" style="container-type: inline-size">
    <form class="agent-form" :aria-busy="streaming" @submit.prevent="emit('submit')">
      <label class="sr-only" for="agent-message">这一轮的问题</label>
      <textarea
        id="agent-message"
        v-model="draft"
        class="message-input"
        name="message"
        rows="2"
        :maxlength="MAX_MESSAGE_CHARACTERS"
        placeholder="问点什么，Agent 会自己去查。Enter 发送，Shift + Enter 换行"
        :aria-invalid="Boolean(inputError)"
        :aria-describedby="inputError ? 'agent-message-error' : 'agent-message-count'"
        @keydown.enter="onEnter"
      ></textarea>

      <div class="composer-bar">
        <div class="bar-left">
          <!-- 系统提示词从 <details> 改成齿轮浮层（Q8）：它是这一档的次要设置，
               摊开在输入框下面会把主操作挤下去。 -->
          <BasePopover v-model:open="promptOpen" label="自定义系统提示词" placement="top-start">
            <template #trigger="{ toggle, attrs }">
              <!-- 角标是齿轮的兄弟而不是子节点：BaseIconButton 没有 position，
                   放进去会定位到更外层的 .base-popover 上，跑到整个浮层的角上。
                   包一层 relative 的 span，定位职责留在本组件里，不去改共享组件。 -->
              <span class="prompt-trigger">
                <BaseIconButton
                  v-bind="attrs"
                  :label="promptOverridden ? '自定义系统提示词（已覆盖）' : '自定义系统提示词'"
                  size="md"
                  @click="toggle"
                >
                  <Settings2 :size="17" aria-hidden="true" />
                </BaseIconButton>
                <!-- 已覆盖时点一个角标：浮层收起后，这是唯一能看出「提示词被改过」的地方。 -->
                <span v-if="promptOverridden" class="prompt-badge" aria-hidden="true"></span>
              </span>
            </template>

            <div class="prompt-panel">
              <p class="prompt-title">自定义系统提示词</p>
              <p class="prompt-note">
                留空表示使用服务端内置的默认提示词。只影响之后发出的轮次，不会被保存。
              </p>
              <textarea
                id="agent-system-prompt"
                v-model="promptDraft"
                class="prompt-input"
                name="system_prompt"
                rows="6"
                :maxlength="MAX_SYSTEM_PROMPT_CHARACTERS"
                aria-label="自定义系统提示词"
                placeholder="留空即使用默认提示词"
              ></textarea>
              <div class="prompt-actions">
                <BaseButton
                  variant="ghost"
                  size="xs"
                  :disabled="defaultPrompt === null"
                  @click="useDefaultPrompt"
                >
                  填入默认提示词
                </BaseButton>
                <BaseButton
                  variant="ghost"
                  size="xs"
                  :disabled="!systemPrompt"
                  @click="emit('update:systemPrompt', '')"
                >
                  清空
                </BaseButton>
              </div>
            </div>
          </BasePopover>

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
  transition: border-color 150ms ease, box-shadow 150ms ease;
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

.message-input::placeholder {
  color: var(--text-muted);
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

.prompt-trigger {
  position: relative;
  display: inline-flex;
}

/* 角标不拦指针事件：它压在齿轮的右上角，能点中的话那一小块就点不开浮层了。 */
.prompt-badge {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  pointer-events: none;
}

.character-count {
  color: var(--text-muted);
  font-family: var(--mono-font);
  font-size: 0.67rem;
  white-space: nowrap;
}

/* 只有临近与超出上界时才需要被看见，那两档自带语气色。默认那档是纯参考信息，
   --text-muted 在这个字号上对比度不足，所以平时不显示，聚焦时才出现。 */
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

.prompt-panel {
  width: min(420px, calc(100vw - 60px));
}

.prompt-title {
  color: var(--text-primary);
  font-size: 0.84rem;
  font-weight: 760;
}

.prompt-note {
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: 0.72rem;
  line-height: 1.55;
}

.prompt-input {
  display: block;
  width: 100%;
  resize: vertical;
  min-height: 150px;
  margin-top: 10px;
  padding: 11px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  outline: none;
  color: var(--text-primary);
  background: var(--surface-base);
  font-family: var(--mono-font);
  font-size: 0.78rem;
  line-height: 1.6;
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease;
}

.prompt-input::placeholder {
  color: var(--text-muted);
}

.prompt-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.prompt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 9px;
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
}
</style>
