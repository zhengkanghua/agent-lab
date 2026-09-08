<script setup lang="ts">
import { computed, useId } from 'vue'
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
</script>

<template>
  <div class="scope-picker">
    <details>
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
  padding: 8px 2px;
  font-size: 0.8rem;
  color: var(--text-secondary);
}
details {
  flex: 1;
  min-width: 0;
}
summary {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
summary::-webkit-details-marker {
  display: none;
}
summary:hover,
.scope-refresh:hover:not(:disabled) {
  color: var(--accent);
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
fieldset {
  border: 0;
  padding: 8px 0 4px;
  margin: 0;
  min-width: 0;
}
.scope-modes,
.scope-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.scope-choices {
  max-height: 180px;
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
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}
.scope-error {
  flex-basis: 100%;
  margin: 0;
  color: var(--warning);
  overflow-wrap: anywhere;
}
</style>
