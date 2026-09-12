<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { ChevronDown, Library, RefreshCw } from '@lucide/vue'
import type { KnowledgeBaseDto } from '@/api/knowledge-bases'
import type { KnowledgeBaseSelection } from '@/api/knowledge-scope'

const props = defineProps<{
  modelValue: KnowledgeBaseSelection
  knowledgeBases: KnowledgeBaseDto[]
  error?: string | null
  loading?: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: KnowledgeBaseSelection]
  refresh: []
}>()
const group = useId()
const ids = computed(() => props.modelValue.knowledge_base_ids ?? [])
const label = computed(() =>
  props.modelValue.mode === 'all'
    ? `所有知识库（${props.knowledgeBases.length}）`
    : props.knowledgeBases
        .filter((item) => ids.value.includes(item.id))
        .map((item) => item.name)
        .join('、') || '选择知识库',
)
function selectMode(mode: 'all' | 'selected') {
  emit('update:modelValue', mode === 'all' ? { mode } : { mode, knowledge_base_ids: [] })
}
function toggle(id: string, checked: boolean) {
  emit('update:modelValue', {
    mode: 'selected',
    knowledge_base_ids: checked ? [...ids.value, id] : ids.value.filter((item) => item !== id),
  })
}

/* 面板是浮层，原生 <details> 只会被自己的 summary 关掉。改成浮层之前它行内展开，
   开着也不挡路；现在它会盖在检索流上面，所以必须补两条退路：点面板外面、按 Esc。
   面板内的点击不关——否则选一个知识库就把面板收走了，多选要重开好几次。 */
const detailsRef = ref<HTMLDetailsElement | null>(null)

function close() {
  if (detailsRef.value) detailsRef.value.open = false
}

function onDocumentPointerDown(event: PointerEvent) {
  const el = detailsRef.value
  if (el?.open && !el.contains(event.target as Node)) close()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && detailsRef.value?.open) {
    close()
    // 焦点还给触发键：面板关了而焦点留在被移除的元素上，Tab 会从文档开头重来。
    detailsRef.value.querySelector('summary')?.focus()
  }
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
  <div class="scope-picker">
    <details ref="detailsRef">
      <summary>
        <Library :size="15" aria-hidden="true" /><span>{{ label }}</span>
        <ChevronDown class="scope-chevron" :size="15" aria-hidden="true" />
      </summary>
      <fieldset>
        <legend class="sr-only">检索知识库范围</legend>
        <div class="scope-modes">
          <label
            ><input
              type="radio"
              :name="group"
              :checked="modelValue.mode === 'all'"
              @change="selectMode('all')"
            />所有知识库</label
          >
          <label
            ><input
              type="radio"
              :name="group"
              :checked="modelValue.mode === 'selected'"
              @change="selectMode('selected')"
            />指定知识库</label
          >
        </div>
        <div v-if="modelValue.mode === 'selected'" class="scope-choices">
          <label
            v-for="item in knowledgeBases"
            :key="item.id"
            :title="item.description ?? undefined"
          >
            <input
              type="checkbox"
              :checked="ids.includes(item.id)"
              @change="toggle(item.id, ($event.target as HTMLInputElement).checked)"
            />
            <span>{{ item.name }}</span>
          </label>
        </div>
      </fieldset>
    </details>
    <button
      type="button"
      class="scope-refresh"
      title="重新加载知识库"
      aria-label="重新加载知识库"
      :disabled="loading"
      @click="emit('refresh')"
    >
      <RefreshCw :size="14" />
    </button>
    <p v-if="error" class="scope-error" :role="loading ? 'status' : 'alert'">{{ error }}</p>
  </div>
</template>

<style scoped>
.scope-picker {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
details {
  flex: 0 1 auto;
  min-width: 0;
  position: relative;
}
/* 安静胶囊：底栏里的一个过滤器入口，不是主操作——描边用弱档、悬停不沾强调色，
   把 accent 的 5% 面积纪律留给发送键。 */
summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
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
.scope-chevron {
  margin-left: auto;
}
details[open] .scope-chevron {
  transform: rotate(180deg);
}
summary span {
  overflow-wrap: anywhere;
}
summary svg {
  flex-shrink: 0;
}
/* 选择面板是浮层，不是行内展开：原位展开会把下方的检索流整体推下去，
   开合一次视线要重新找位置。浮层盖在上面，检索流一动不动。
   不配 Portal——它就贴在自己的触发键下面，脱离文档流反而要手动跟随滚动。 */
fieldset {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: var(--z-popover);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  padding: 12px 14px;
  margin: 0;
  /* 宽随内容、不拉伸到胶囊同宽：触发器收成胶囊后，全宽面板会大得突兀。 */
  min-width: 260px;
  max-width: min(340px, 80vw);
}
.scope-modes,
.scope-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.scope-choices {
  max-height: 240px;
  overflow-y: auto;
  margin-top: 10px;
}
label {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  min-height: 32px;
  overflow-wrap: anywhere;
}
input {
  accent-color: var(--accent);
  flex-shrink: 0;
}
.scope-refresh {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.scope-refresh:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--surface-hover);
}
.scope-error {
  flex-basis: 100%;
  margin: 0;
  color: var(--warning);
  overflow-wrap: anywhere;
}

/* 触屏没有 hover，指针也不精确：把这一组控件撑到 44px 的可点高度，
   复选框本身也放大。键盘与鼠标用户的上限、间距不受影响。 */
@media (pointer: coarse) {
  summary,
  label {
    min-height: var(--tap-target);
  }

  .scope-refresh {
    width: var(--tap-target);
    height: var(--tap-target);
  }

  input {
    width: 18px;
    height: 18px;
  }

  .scope-choices {
    gap: 4px 20px;
  }
}
</style>
