<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bot, ChevronDown, RefreshCw } from '@lucide/vue'
import type { AvailableLlmModelDto } from '@/api/llm-models'
import type { ChatModelChoice } from '../composables/useChatModel'

/*
 * 会话里选模型的选择器。形状与知识库范围选择器（shared/ui/KnowledgeBaseScopePicker.vue）同源：
 * 一枚安静胶囊 + 浮层面板。三处不同，都是本页特有的：
 *
 * 1. **每次打开都重新拉目录**（emit open，由调用方 refresh）。会话列表与目录加载后是有缓存的，
 *    不重拉的话用户会看到一个刚下线的模型、或者刚建的模型迟迟选不到。
 * 2. **失效态**：当前选择不在可选目录里时，面板里如实说「原来是 xxx，该模型已不可用，请重新
 *    选择」，并把可选的那些摆在下面当重选入口。**不静默回落**成默认模型——那样用户以为自己
 *    还在用原来的模型，平台已经悄悄换了一个。
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
  if (!props.loading && props.models.length === 0)
    return '当前没有可用的模型，请联系管理员配置。'
  return ''
})

const detailsRef = ref<HTMLDetailsElement | null>(null)

function close(): void {
  if (detailsRef.value) detailsRef.value.open = false
}

/* 面板是浮层，原生 <details> 只会被自己的 summary 关掉，所以要补两条退路：点外面、按 Esc。
   面板内的点击不关——否则选一条模型就把面板收走了。 */
function onToggle(): void {
  if (detailsRef.value?.open) emit('open')
}

function onDocumentPointerDown(event: PointerEvent): void {
  const el = detailsRef.value
  if (el?.open && !el.contains(event.target as Node)) close()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && detailsRef.value?.open) {
    close()
    // 焦点还给触发键：面板关了而焦点留在被移除的元素上，Tab 会从文档开头重来。
    detailsRef.value.querySelector('summary')?.focus()
  }
}

function choose(model: AvailableLlmModelDto): void {
  emit('update:modelValue', { id: model.id, displayName: model.display_name })
  close()
}

onMounted(() => {
  // pointerdown 而不是 click：后者要等抬手，拖拽选择文本时也会误触发。
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="model-picker">
    <details ref="detailsRef" @toggle="onToggle">
      <summary :class="{ 'is-unavailable': unavailable }" :title="label">
        <Bot :size="15" aria-hidden="true" />
        <span class="model-label">{{ label }}</span>
        <span v-if="unavailable" class="model-warning" role="status">已不可用</span>
        <ChevronDown class="model-chevron" :size="15" aria-hidden="true" />
      </summary>
      <fieldset>
        <legend class="sr-only">选择对话使用的模型</legend>
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
      </fieldset>
    </details>
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

details {
  position: relative;
  flex: 0 1 auto;
  min-width: 0;
}

/* 安静胶囊：与同排的知识库范围胶囊、徽章胶囊同档尺寸（38px），把 accent 的 5% 面积纪律
   留给发送键。 */
summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-height: 38px;
  padding: 4px 11px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

summary::-webkit-details-marker {
  display: none;
}

summary:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
  background: var(--surface-hover);
}

summary:active {
  background: var(--surface-sunken-hover);
}

summary svg {
  flex-shrink: 0;
}

/* 失效时那枚胶囊换个语气色：用户不打开面板也该看出「这个选择有问题」。 */
summary.is-unavailable {
  border-color: var(--warning);
}

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

.model-chevron {
  margin-left: auto;
}

details[open] .model-chevron {
  transform: rotate(180deg);
}

fieldset {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: var(--z-popover);
  min-width: 260px;
  max-width: min(340px, 80vw);
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
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
  summary {
    min-height: var(--tap-target);
  }

  .model-choice {
    min-height: var(--tap-target);
  }

  .model-refresh {
    width: var(--tap-target);
    height: var(--tap-target);
  }
}
</style>
