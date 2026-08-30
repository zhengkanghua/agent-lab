<script setup lang="ts">
import { computed } from 'vue'
import { LoaderCircle, MessageSquarePlus, Send, Settings2, Square } from '@lucide/vue'
import { MAX_MESSAGE_CHARACTERS, MAX_SYSTEM_PROMPT_CHARACTERS } from '../model/agent-validation'

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
  <section class="agent-composer" aria-labelledby="agent-composer-title">
    <div class="composer-heading">
      <p>对话输入</p>
      <h2 id="agent-composer-title">向 Agent 提问</h2>
    </div>

    <form class="agent-form" :aria-busy="streaming" @submit.prevent="emit('submit')">
      <label class="field-label" for="agent-message">这一轮的问题</label>
      <textarea
        id="agent-message"
        v-model="draft"
        class="message-input"
        name="message"
        rows="5"
        :maxlength="MAX_MESSAGE_CHARACTERS"
        placeholder="例如：最近有哪些关于利率的报道？请给出来源。"
        :aria-invalid="Boolean(inputError)"
        :aria-describedby="inputError ? 'agent-message-error' : 'agent-message-count'"
        @keydown.enter="onEnter"
      ></textarea>

      <div class="input-meta">
        <span class="hint">Enter 发送，Shift + Enter 换行</span>
        <span id="agent-message-count" class="character-count" :class="counterTone">
          还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
        </span>
      </div>

      <details class="prompt-options">
        <summary>
          <span class="control-label">
            <Settings2 :size="16" aria-hidden="true" />
            自定义系统提示词
          </span>
          <b>{{ systemPrompt.trim() ? '已覆盖' : '用默认' }}</b>
        </summary>
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
          <button
            type="button"
            class="text-button"
            :disabled="defaultPrompt === null"
            @click="useDefaultPrompt"
          >
            填入默认提示词
          </button>
          <button
            type="button"
            class="text-button"
            :disabled="!systemPrompt"
            @click="emit('update:systemPrompt', '')"
          >
            清空
          </button>
        </div>
      </details>

      <div class="composer-actions">
        <button
          v-if="hasHistory"
          type="button"
          class="secondary-button"
          :disabled="streaming"
          @click="emit('new-conversation')"
        >
          <MessageSquarePlus :size="17" aria-hidden="true" />
          <span>新会话</span>
        </button>
        <button v-if="streaming" type="button" class="stop-button" @click="emit('cancel')">
          <Square :size="16" aria-hidden="true" />
          <span>停止生成</span>
        </button>
        <button v-else class="send-button" type="submit" :disabled="!canSend">
          <LoaderCircle v-if="streaming" class="spin" :size="18" aria-hidden="true" />
          <Send v-else :size="17" stroke-width="2.3" aria-hidden="true" />
          <span>发送</span>
        </button>
      </div>
    </form>

    <p v-if="inputError" id="agent-message-error" class="field-error" role="alert">
      {{ inputError }}
    </p>
  </section>
</template>

<style scoped>
.agent-composer {
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
}

.composer-heading h2 {
  margin-top: 4px;
  color: var(--ink-950);
  font-family: var(--display-font);
  font-size: 1.28rem;
  font-weight: 760;
  line-height: 1.3;
}

.agent-form {
  display: grid;
}

.field-label {
  margin-bottom: 7px;
  color: var(--ink-800);
  font-size: 0.8rem;
  font-weight: 700;
}

.message-input,
.prompt-input {
  display: block;
  width: 100%;
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

.message-input {
  min-height: 132px;
}

.prompt-input {
  min-height: 150px;
  margin-top: 10px;
  font-family: var(--mono-font);
  font-size: 0.78rem;
  line-height: 1.6;
}

.message-input::placeholder,
.prompt-input::placeholder {
  color: var(--ink-500);
}

.message-input:hover,
.prompt-input:hover {
  border-color: #bac4c0;
}

.message-input:focus,
.prompt-input:focus {
  border-color: var(--source-500);
  background: var(--paper-50);
  box-shadow: 0 0 0 4px var(--source-100);
}

.input-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 30px;
  padding-top: 7px;
}

.hint {
  color: var(--ink-500);
  font-size: 0.68rem;
}

.character-count {
  color: var(--ink-500);
  font-family: var(--mono-font);
  font-size: 0.67rem;
}

.character-count.is-near {
  color: var(--warning-600);
}

.character-count.is-over {
  color: var(--danger-600);
}

.prompt-options {
  margin-top: 6px;
  padding-bottom: 12px;
  border-top: 1px solid var(--paper-200);
  border-bottom: 1px solid var(--paper-200);
  color: var(--ink-700);
  font-size: 0.8rem;
}

.prompt-options summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  cursor: pointer;
  list-style-position: outside;
}

.prompt-options summary b {
  color: var(--ink-800);
  font-size: 0.75rem;
}

.control-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.control-label svg {
  color: var(--source-600);
}

.prompt-note {
  color: var(--ink-500);
  font-size: 0.72rem;
  line-height: 1.55;
}

.prompt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 14px;
  margin-top: 9px;
}

.text-button {
  border: 0;
  color: var(--signal-600);
  background: transparent;
  font-size: 0.74rem;
  font-weight: 700;
}

.text-button:disabled {
  color: var(--ink-500);
  opacity: 0.6;
}

.composer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.secondary-button,
.send-button,
.stop-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 44px;
  padding: 0 16px;
  border: 0;
  border-radius: var(--radius-sm);
  font-weight: 760;
  transition:
    background-color 150ms ease,
    color 150ms ease,
    transform 150ms ease;
}

.secondary-button {
  color: var(--ink-700);
  background: var(--paper-200);
}

.secondary-button:hover:not(:disabled) {
  color: var(--ink-950);
  background: var(--paper-300);
}

.secondary-button:disabled {
  opacity: 0.55;
}

.send-button {
  flex: 1;
  color: var(--paper-50);
  background: var(--signal-500);
}

.send-button:hover:not(:disabled) {
  background: var(--signal-600);
  transform: translateY(-1px);
}

.send-button:disabled {
  cursor: not-allowed;
  opacity: 0.72;
}

.stop-button {
  flex: 1;
  color: var(--danger-600);
  background: var(--danger-100);
}

.field-error {
  margin-top: 12px;
  color: var(--danger-600);
  font-size: 0.8rem;
  font-weight: 650;
}

/* .spin 见 styles/components/motion.css。 */

@media (max-width: 420px) {
  .agent-composer {
    padding: 18px 15px;
  }

  .composer-actions {
    flex-wrap: wrap;
  }
}
</style>
