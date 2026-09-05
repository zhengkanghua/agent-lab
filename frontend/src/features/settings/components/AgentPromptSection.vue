<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref } from 'vue'
import { Bot, Check } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseTextarea from '@/shared/ui/BaseTextarea.vue'
import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'
import { validateAgentSystemPrompt } from '../model/preferences'
import { useDefaultAgentPrompt } from '../composables/useDefaultAgentPrompt'
import { usePreferences } from '../composables/usePreferences'

/**
 * 设置中心 · Agent 偏好分区（仅超级用户）。
 *
 * 自定义系统提示词从对话输入区的折叠面板迁来：它是「改变模型行为」的配置，不是一条
 * 消息——商业产品的同类能力（自定义指令）都在设置页里，可发现、可持久、可恢复默认。
 * 保存进本浏览器的偏好 store，之后每一轮对话（任何会话）发送时都会带上。
 *
 * 编辑走草稿 + 显式保存：提示词是大段文本，即时生效会让「改一半」的半成品被下一轮
 * 对话带出去。保存成功给一条 2.5 秒的内联确认——一个轻量动作不值得动用全局通知。
 */
const { preferences } = usePreferences()
const { defaultPrompt, load: loadDefaultPrompt } = useDefaultAgentPrompt()

const draft = ref(preferences.agentSystemPrompt)
const savedFlash = ref(false)
let savedFlashTimer: ReturnType<typeof setTimeout> | undefined

onMounted(() => {
  void loadDefaultPrompt()
})

onScopeDispose(() => clearTimeout(savedFlashTimer))

const validationError = computed(() => validateAgentSystemPrompt(draft.value))

/** 与已保存值不同才算改过：保存键是「提交差异」的开关，不是常亮装饰。 */
const isDirty = computed(() => draft.value !== preferences.agentSystemPrompt)

const canSave = computed(() => isDirty.value && validationError.value === null)

const statusLabel = computed(() =>
  preferences.agentSystemPrompt.trim().length > 0 ? '已启用自定义提示词' : '使用服务端默认提示词',
)

const remainingCharacters = computed(() => MAX_SYSTEM_PROMPT_CHARACTERS - draft.value.length)

function save(): void {
  if (!canSave.value) return
  preferences.agentSystemPrompt = draft.value
  savedFlash.value = true
  clearTimeout(savedFlashTimer)
  savedFlashTimer = setTimeout(() => {
    savedFlash.value = false
  }, 2500)
}

/** 用服务端默认的那份覆盖草稿，仍需点保存才生效。 */
function fillDefault(): void {
  if (defaultPrompt.value !== null) draft.value = defaultPrompt.value
}

/** 清空草稿并立即保存：这是「回到默认行为」的显式动作，两步并一步不用再点保存。 */
function clearPrompt(): void {
  draft.value = ''
  preferences.agentSystemPrompt = ''
}
</script>

<template>
  <section class="agent-prefs" aria-labelledby="agent-prefs-heading">
    <h2 id="agent-prefs-heading" class="section-heading">Agent 偏好</h2>
    <p class="section-intro">
      自定义系统提示词决定 Agent 的行为方式，之后每一轮对话都会带上它。只保存在当前浏览器，
      不会被同步；留空表示使用服务端内置的默认提示词。
    </p>

    <div class="editor-card">
      <div class="status-row">
        <span class="status-badge" :class="{ 'is-active': preferences.agentSystemPrompt }">
          <Bot :size="13" aria-hidden="true" />
          {{ statusLabel }}
        </span>
        <Transition name="flash">
          <span v-if="savedFlash" class="saved-note" role="status">
            <Check :size="13" aria-hidden="true" />
            已保存，下一轮对话生效
          </span>
        </Transition>
      </div>

      <BaseField
        id="agent-system-prompt"
        label="自定义系统提示词"
        :error="validationError ?? undefined"
      >
        <template #default="{ control }">
          <BaseTextarea
            v-bind="control"
            v-model="draft"
            class="prompt-editor"
            mono
            :rows="10"
            :maxlength="MAX_SYSTEM_PROMPT_CHARACTERS"
            placeholder="留空即使用默认提示词"
          />
        </template>
        <template #hint>
          <span
            class="char-count"
            :class="{ 'is-near': remainingCharacters < 200, 'is-over': remainingCharacters < 0 }"
          >
            还可输入 {{ remainingCharacters.toLocaleString('zh-CN') }} 个字符
          </span>
        </template>
      </BaseField>

      <div class="editor-actions">
        <BaseButton variant="primary" size="sm" :disabled="!canSave" @click="save">
          保存
        </BaseButton>
        <BaseButton
          variant="ghost"
          size="sm"
          :disabled="defaultPrompt === null"
          @click="fillDefault"
        >
          填入默认提示词
        </BaseButton>
        <BaseButton
          variant="ghost"
          size="sm"
          :disabled="!preferences.agentSystemPrompt && !draft"
          @click="clearPrompt"
        >
          清空并恢复默认
        </BaseButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.section-heading {
  margin: 0 0 var(--space-3);
  color: var(--text-primary);
  font-size: var(--text-2xl);
  font-weight: 760;
}

.section-intro {
  margin: 0 0 var(--space-6);
  max-width: 46ch;
  color: var(--text-secondary);
  font-size: 0.88rem;
  line-height: 1.7;
}

.editor-card {
  max-width: 44rem;
  padding: var(--space-5);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
}

.status-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  color: var(--text-secondary);
  background: var(--surface-sunken);
  font-size: 0.72rem;
  font-weight: 700;
}

/* 已启用自定义时点亮徽章：扫一眼就知道现在的对话在用什么行为。 */
.status-badge.is-active {
  border-color: var(--accent-soft);
  color: var(--accent);
  background: var(--accent-soft);
}

.saved-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-success);
  font-size: 0.76rem;
  font-weight: 700;
}

.flash-enter-active,
.flash-leave-active {
  transition:
    opacity var(--duration-normal) var(--ease-out-smooth),
    transform var(--duration-normal) var(--ease-out-smooth);
}

.flash-enter-from,
.flash-leave-to {
  opacity: 0;
  transform: translateY(-3px);
}

.prompt-editor {
  min-height: 220px;
}

.char-count {
  font-family: var(--mono-font);
  font-size: 0.7rem;
}

.char-count.is-near {
  color: var(--warning);
}

.char-count.is-over {
  color: var(--danger);
}

.editor-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
}
</style>
