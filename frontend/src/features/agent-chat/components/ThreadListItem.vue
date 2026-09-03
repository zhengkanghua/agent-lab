<script setup lang="ts">
import { computed } from 'vue'
import { Trash2 } from '@lucide/vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'

const props = defineProps<{
  thread: AgentThreadSummaryDto
  active: boolean
  deleting: boolean
}>()

const emit = defineEmits<{ open: []; remove: [] }>()

/**
 * 相对时间，精度按距离递减。
 *
 * 用相对时间而不是完整时间戳：列表里要回答的是「哪个是我刚才聊的」，而不是「具体几点几分」。
 * 超过一周才退回日期，因为那时「7 天前」已经不比日期更好认了。
 */
const relativeTime = computed(() => {
  const then = Date.parse(props.thread.last_active_at)
  if (Number.isNaN(then)) return ''

  const minutes = Math.floor((Date.now() - then) / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`

  return new Date(then).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
})

/** 完整时间戳留在 title 里：需要精确时间的人悬停就能看到，列表本身保持清爽。 */
const exactTime = computed(() => {
  const then = Date.parse(props.thread.last_active_at)
  return Number.isNaN(then) ? '' : new Date(then).toLocaleString('zh-CN')
})
</script>

<template>
  <li class="thread-item" :class="{ 'is-active': active, 'is-deleting': deleting }">
    <!-- 整行是一个按钮，不是 router-link 包着的一整块：里面还有个删除按钮，
         嵌套可交互元素会让键盘 Tab 顺序和读屏播报都变得混乱。跳转由页面处理。 -->
    <button
      type="button"
      class="open-button"
      :aria-current="active ? 'true' : undefined"
      :disabled="deleting"
      @click="emit('open')"
    >
      <!-- 标题是用户提问的前 60 字，用文本插值渲染，绝不 v-html。 -->
      <span class="title">{{ thread.title }}</span>
      <span class="time" :title="exactTime">{{ relativeTime }}</span>
    </button>

    <BaseIconButton
      class="remove-button"
      label="删除这个会话"
      size="sm"
      :loading="deleting"
      busy-cursor
      @click="emit('remove')"
    >
      <Trash2 :size="14" aria-hidden="true" />
    </BaseIconButton>
  </li>
</template>

<style scoped>
.thread-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px;
  align-items: center;
  border-radius: var(--radius-sm);
  transition: background-color 150ms ease;
}

.thread-item:hover {
  background: var(--surface-raised);
}

/* 当前会话用左侧强调条 + 底色标记，不只靠底色：底色差异在深色主题下很弱，
   而这一行是「我在哪」的唯一指示。 */
.thread-item.is-active {
  background: var(--surface-raised);
  box-shadow: inset 2px 0 0 var(--accent);
}

.thread-item.is-deleting {
  opacity: 0.6;
}

.open-button {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 9px 8px 9px 11px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.open-button:disabled {
  cursor: wait;
}

/* 省略号交给 CSS，不在后端截断时加：宽屏放得下整句时不该带个多余的点。 */
.title {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 0.83rem;
  line-height: 1.45;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.is-active .title {
  font-weight: 650;
}

.time {
  color: var(--text-tertiary);
  font-size: 0.68rem;
}

/* 删除键平时隐形，悬停或键盘聚焦到行内任何元素时才出现：常驻会让列表变成一排垃圾桶，
   而删除是低频且不可逆的动作。用 opacity 而不是 display 切换，这样它始终在 Tab 序里——
   只靠键盘操作的人否则永远到不了它。 */
.remove-button {
  margin-right: 5px;
  opacity: 0;
  transition: opacity 150ms ease;
}

.thread-item:hover .remove-button,
.thread-item:focus-within .remove-button,
.remove-button[aria-busy='true'] {
  opacity: 1;
}
</style>
