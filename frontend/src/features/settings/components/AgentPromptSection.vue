<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { Bot, Check } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseTextarea from '@/shared/ui/BaseTextarea.vue'
import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'
import { validateAgentSystemPrompt } from '../model/preferences'
import { useDefaultAgentPrompt } from '../composables/useDefaultAgentPrompt'
import { usePreferences } from '../composables/usePreferences'

/**
 * 设置中心 · Agent 偏好分区。
 *
 * 自定义系统提示词从对话输入区的折叠面板迁来：它是「改变模型行为」的配置，不是一条
 * 消息——商业产品的同类能力（自定义指令）都在设置页里，可发现、可持久、可恢复默认。
 * 保存在账号上，**作为新会话的初始提示词**：会话建立时会被快照进那个会话，此后不变。
 *
 * 编辑走草稿 + 显式保存：提示词是大段文本，即时生效会让「改一半」的半成品被新会话带走。
 * 保存成功给一条 2.5 秒的内联确认——一个轻量动作不值得动用全局通知。
 */
const { preferences, save: savePreferences } = usePreferences()
const { defaultPrompt, load: loadDefaultPrompt } = useDefaultAgentPrompt()

// 草稿归设置页持有，分区切换不会丢失，也不会提前改变实际使用的提示词。
const draft = defineModel<string>({ required: true })
const savedFlash = ref(false)
const saveError = ref('')
const saving = ref(false)
let savedFlashTimer: ReturnType<typeof setTimeout> | undefined

onMounted(() => {
  void loadDefaultPrompt()
})

onScopeDispose(() => clearTimeout(savedFlashTimer))

const validationError = computed(() => validateAgentSystemPrompt(draft.value))

/** 与已保存值不同才算改过：保存键是「提交差异」的开关，不是常亮装饰。 */
const isDirty = computed(() => draft.value !== preferences.agentSystemPrompt)

const canSave = computed(() => isDirty.value && validationError.value === null && !saving.value)

watch(draft, () => {
  savedFlash.value = false
  saveError.value = ''
})

const statusLabel = computed(() =>
  preferences.agentSystemPrompt.trim().length > 0 ? '已启用自定义提示词' : '使用服务端默认提示词',
)

const remainingCharacters = computed(() => MAX_SYSTEM_PROMPT_CHARACTERS - draft.value.length)

async function save(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  saveError.value = ''
  try {
    // 写的是完整一份偏好：接口是整体覆盖，提示词与两个数量参数一起提交。
    await savePreferences({ ...preferences, agentSystemPrompt: draft.value })
    savedFlash.value = true
    clearTimeout(savedFlashTimer)
    savedFlashTimer = setTimeout(() => {
      savedFlash.value = false
    }, 2500)
  } catch {
    // 失败时不动草稿也不动已保存值：用户还能再点一次保存，不会丢掉刚写的内容。
    saveError.value = '保存失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

/** 草稿退回已保存值。与「清空并恢复默认」不同：它不提交，只是放弃这次编辑。 */
function discardDraft(): void {
  draft.value = preferences.agentSystemPrompt
}

/** 用服务端默认的那份覆盖草稿，仍需点保存才生效。 */
function fillDefault(): void {
  if (defaultPrompt.value !== null) draft.value = defaultPrompt.value
}

/** 清空草稿并立即保存：这是「回到默认行为」的显式动作，两步并一步不用再点保存。 */
async function clearPrompt(): Promise<void> {
  draft.value = ''
  await save()
}
</script>

<template>
  <section class="agent-prefs" aria-labelledby="agent-prefs-heading">
    <h2 id="agent-prefs-heading" class="section-heading">Agent 偏好</h2>
    <p class="section-intro">
      自定义系统提示词决定 Agent 的行为方式。它作为新会话的初始提示词，会话建立时定下，
      已开始的会话不受影响。保存在你的账号上，换设备登录也一致；留空表示使用服务端内置的
      默认提示词。
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
            已保存，新会话生效
          </span>
        </Transition>
        <span v-if="isDirty" class="unsaved-note" role="status">尚未保存</span>
        <span v-if="saveError" class="save-error" role="alert">{{ saveError }}</span>
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
        <BaseButton variant="ghost" size="sm" :disabled="!isDirty" @click="discardDraft">
          放弃修改
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
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
}

.section-intro {
  margin: 0 0 var(--space-6);
  max-width: 46ch;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
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
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.unsaved-note {
  color: var(--warning);
  font-size: var(--fs-xs);
}

.save-error {
  color: var(--danger);
  font-size: var(--fs-xs);
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
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
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
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
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
  font-size: var(--fs-xs);
}

.char-count.is-near {
  color: var(--warning);
}

.char-count.is-over {
  color: var(--danger);
}

/* 底部吸附保存条（2026-09 重设计 P4）：浮层里内容滚动时保存键始终可见，
   整页形态下吸附文档滚动，同效。负 margin 把这一行拉到卡片边缘并吃掉卡片的
   底 padding，sticky bottom 才贴得住滚动视口。 */
.editor-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-4) calc(-1 * var(--space-5)) calc(-1 * var(--space-5));
  padding: var(--space-3) var(--space-5) var(--space-4);
  background: var(--surface-raised);
  border-top: 1px solid var(--border-subtle);
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
}
</style>
