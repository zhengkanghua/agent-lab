<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, RefreshCw } from '@lucide/vue'
import type { AvailableLlmModelDto } from '@/api/llm-models'
import BasePopover from '@/shared/ui/BasePopover.vue'
import type { ChatModelChoice } from '../composables/useChatModel'

/*
 * 会话里选模型的选择器。与知识库范围选择器（shared/ui/KnowledgeBaseScopePicker.vue）
 * 同源：都是 BasePopover 的一枚胶囊 + 一块面板，开合、定位、点外关闭、Esc 都在那边。
 * 三处不同，都是本页特有的：
 *
 * 1. **每次打开都重新拉目录**（BasePopover 的 open 事件，由调用方 refresh）。会话列表与
 *    目录加载后是有缓存的，不重拉的话用户会看到一个刚下线的模型、或者刚建的模型迟迟选不到。
 * 2. **失效态**：当前选择不在可选目录里时，面板里如实说「原来是 xxx，该模型已不可用，请重新
 *    选择」，并把可选的那些摆在下面当重选入口。**不静默回落**成默认模型——那样用户以为自己
 *    还在用原来的模型，平台已经悄悄换了一个。胶囊本身也换成提醒描边，不打开也看得出来。
 * 3. 单选：一条模型就是一个选项，没有「全部/指定」那种模式。
 */

const props = defineProps<{
  /** 会话当前的选择；为 null 表示这个会话没选过，提问时用目录里的默认模型。 */
  modelValue: ChatModelChoice | null
  models: AvailableLlmModelDto[]
  /** 目录拉取失败时的提示；有它就不说「没有可用的模型」。 */
  error?: string | null
  loading?: boolean
  /** 当前选择已不在可选目录里（停用、渠道停用、或已经不存在）。 */
  unavailable?: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: ChatModelChoice]
  /** 面板打开了，调用方据此重新拉目录。 */
  open: []
  refresh: []
}>()

const open = ref(false)

const label = computed(() =>
  props.modelValue === null ? '默认模型' : (props.modelValue.displayName ?? '原选中的模型'),
)

/** 面板里那一句状态：没得选、读不到目录、当前选择失效，各说各的。 */
const statusText = computed(() => {
  if (props.error) return props.error
  if (props.unavailable) {
    const name = props.modelValue?.displayName
    return name
      ? `原来是 ${name}，该模型已不可用，请重新选择。`
      : '原来是这个模型，它已不可用，请重新选择。'
  }
  if (!props.loading && props.models.length === 0) return '当前没有可用的模型，请联系管理员配置。'
  return ''
})

function choose(model: AvailableLlmModelDto): void {
  emit('update:modelValue', { id: model.id, displayName: model.display_name })
  // 单选：选完就收面板。范围选择器是多选，所以那边不关。
  open.value = false
}
</script>

<template>
  <div class="model-picker">
    <BasePopover
      v-model:open="open"
      label="选择对话使用的模型"
      :tone="unavailable ? 'warning' : 'default'"
      @open="emit('open')"
    >
      <template #trigger>
        <Bot :size="15" aria-hidden="true" />
        <span class="model-label">{{ label }}</span>
        <span v-if="unavailable" class="model-warning" role="status">已不可用</span>
      </template>

      <p v-if="statusText" class="model-status" :role="error ? 'alert' : 'status'">
        {{ statusText }}
      </p>
      <div class="model-choices">
        <button
          v-for="item in models"
          :key="item.id"
          type="button"
          class="model-choice"
          :class="{ 'is-selected': item.id === modelValue?.id }"
          :aria-pressed="item.id === modelValue?.id"
          @click="choose(item)"
        >
          <span class="model-name">{{ item.display_name }}</span>
          <span class="model-meta">
            {{ item.provider_name }} · 上下文窗口 {{ item.context_window }}
          </span>
        </button>
        <p v-if="loading && models.length === 0" class="model-status" role="status">
          正在加载模型目录…
        </p>
      </div>
      <p v-if="modelValue" class="model-note">
        没选模型时会用目录里的默认模型；这里选过的会被这个会话记住，也能随时换。
      </p>
    </BasePopover>

    <button
      type="button"
      class="model-refresh"
      :class="{ 'is-loading': loading }"
      title="重新加载模型目录"
      aria-label="重新加载模型目录"
      :aria-busy="loading || undefined"
      :disabled="loading"
      @click="emit('refresh')"
    >
      <RefreshCw :size="14" />
    </button>
  </div>
</template>

<style scoped>
.model-picker {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}

/* 触发键的胶囊皮肤、失效描边、箭头旋转、触屏高度都归共享层 popover.css。
   这里只剩胶囊里那两段内容：模型名要能截断（名字比知识库名长得多），
   以及「已不可用」那枚小徽章。 */
.model-label {
  max-width: 12em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-warning {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: var(--radius-pill);
  color: var(--warning);
  background: var(--surface-sunken);
  font-size: var(--fs-xs);
}

.model-status {
  margin: 0 0 10px;
  color: var(--warning);
  font-size: var(--fs-xs);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.model-choices {
  display: grid;
  gap: 2px;
  max-height: 240px;
  overflow-y: auto;
}

.model-choice {
  display: grid;
  gap: 2px;
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.model-choice:hover {
  background: var(--surface-hover);
}

.model-choice:active {
  background: var(--surface-sunken-hover);
}

/* 当前那一条用强调色 + 描边标出来：面板里没有单选圆点，只靠底色区分不够。 */
.model-choice.is-selected {
  color: var(--accent);
  background: var(--surface-sunken);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.model-name {
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}

.model-meta {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

.model-note {
  margin: 10px 0 0;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

/* 刷新键与知识库范围那枚同档（34px）：裸图标键，不必跟到胶囊的 38px。 */
.model-refresh {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.model-refresh:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.model-refresh:active:not(:disabled) {
  background: var(--surface-sunken-hover);
}

/* 这一枚的 disabled 只有一个来源：正在重新拉目录。所以它不是「不可用」而是「正在忙」——
   压暗之外还让图标转起来，指针给 wait。 */
.model-refresh:disabled {
  color: var(--text-tertiary);
  cursor: wait;
}

.model-refresh.is-loading svg {
  animation: model-refresh-spin 900ms linear infinite;
}

@keyframes model-refresh-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .model-refresh.is-loading svg {
    animation: none;
    opacity: 0.55;
  }
}

@media (pointer: coarse) {
  .model-choice {
    min-height: var(--tap-target);
  }

  .model-refresh {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}
</style>
