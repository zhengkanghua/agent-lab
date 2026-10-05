<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { Library, RefreshCw } from '@lucide/vue'
import type { KnowledgeBaseDto } from '@/api/knowledge-bases'
import type { KnowledgeBaseSelection } from '@/api/knowledge-scope'
import BasePopover from './BasePopover.vue'

/* 知识库范围选择器：输入坞底栏里的一枚胶囊，点开是「全部 / 指定 + 多选」面板。
 *
 * 开合、定位、点外关闭、Esc 关闭、焦点归还全部归 BasePopover——这里只留三件事：
 * 胶囊上显示什么、面板里点一下改出什么选择、以及刷新键。
 *
 * 面板曾经是行内展开的 `<details>`，后来改成 absolute 浮层。那次改动的方向是对的
 * （行内展开会把下边的检索流整体推下去），但落点写死了「触发键下方 4px」，而这枚
 * 胶囊坐在贴底的输入坞里——面板于是整块展开到屏幕外，2026-10 实测只有 29% 露在
 * 视口内。现在落点由可用空间算，下方放不下就往上翻。
 */

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

const open = ref(false)
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
    <!-- 选一个知识库不关面板：多选要连选好几个，关掉就得重开。点面板外或 Esc 才关，
         这两条现在归 BasePopover。 -->
    <BasePopover v-model:open="open" label="检索知识库范围">
      <template #trigger>
        <Library :size="15" aria-hidden="true" />
        <span class="scope-label">{{ label }}</span>
      </template>

      <div class="scope-modes" role="radiogroup" aria-label="检索范围模式">
        <label>
          <input
            type="radio"
            :name="group"
            :checked="modelValue.mode === 'all'"
            @change="selectMode('all')"
          />所有知识库
        </label>
        <label>
          <input
            type="radio"
            :name="group"
            :checked="modelValue.mode === 'selected'"
            @change="selectMode('selected')"
          />指定知识库
        </label>
      </div>

      <div v-if="modelValue.mode === 'selected'" class="scope-choices">
        <label v-for="item in knowledgeBases" :key="item.id" :title="item.description ?? undefined">
          <input
            type="checkbox"
            :checked="ids.includes(item.id)"
            @change="toggle(item.id, ($event.target as HTMLInputElement).checked)"
          />
          <span>{{ item.name }}</span>
        </label>
      </div>
    </BasePopover>

    <button
      type="button"
      class="scope-refresh"
      :class="{ 'is-loading': loading }"
      title="重新加载知识库"
      aria-label="重新加载知识库"
      :aria-busy="loading || undefined"
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

/* 触发键的皮肤（胶囊、悬停、按下、箭头旋转、触屏高度）归共享层 popover.css。
   这里只剩胶囊里那段文字：库名可能很长，要能换行而不是把胶囊撑破。 */
.scope-label {
  overflow-wrap: anywhere;
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
  /* 这一整行都是可点标签（点库名也能选中），指针要说清楚。 */
  cursor: pointer;
}

input {
  accent-color: var(--accent);
  flex-shrink: 0;
}

/* 刷新键是裸图标、没有描边也没有底，与旁边那枚胶囊不属于同一类，
   所以尺寸不必跟到 38；但 28px 低于本仓库给行内图标键定的那一档
   （BaseIconButton 的 sm = 34px，会话列表的删除键同档），
   在一行 38px 的控件中间显得缩着。对齐到 34px。 */
.scope-refresh {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.scope-refresh:hover:not(:disabled) {
  color: var(--text-primary);
  background: var(--surface-hover);
}

.scope-refresh:active:not(:disabled) {
  background: var(--surface-sunken-hover);
}

/* 这一枚的 disabled 只有一个来源：后台正在重新拉取知识库。所以它不是「不可用」，
   而是「正在忙」——压暗之外还让图标转起来，指针给 wait。少了这一段，加载中
   它和可用状态长得一模一样，点下去毫无反应，看起来像坏了。 */
.scope-refresh:disabled {
  color: var(--text-tertiary);
  cursor: wait;
}

.scope-refresh.is-loading svg {
  animation: scope-refresh-spin 900ms linear infinite;
}

@keyframes scope-refresh-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .scope-refresh.is-loading svg {
    animation: none;
    opacity: 0.55;
  }
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
