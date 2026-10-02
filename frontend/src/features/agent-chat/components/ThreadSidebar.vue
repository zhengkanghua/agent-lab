<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, RotateCcw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import BasePager from '@/shared/ui/BasePager.vue'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'
import type { AgentErrorPresentation } from '../model/agent-error'
import type { ThreadListState } from '../composables/useThreadList'
import ThreadListItem from './ThreadListItem.vue'

const props = defineProps<{
  threads: readonly AgentThreadSummaryDto[]
  total: number
  activeThreadId: string | null
  listState: ThreadListState
  listError: AgentErrorPresentation | null
  hasMore: boolean
  hasPrevious: boolean
  isEmpty: boolean
  deletingThreadIds: ReadonlySet<string>
}>()

const emit = defineEmits<{
  open: [threadId: string]
  remove: [thread: AgentThreadSummaryDto]
  reload: []
  nextPage: []
  previousPage: []
}>()

/* 会话按最近活动时间分组（2026-09 重设计 P3）：今天 / 昨天 / 本周 / 更早。
 *
 * 「本周」用滑动 7 天而不是自然周：自然周口径下，周日回头看上周六的会话会被撇进
 * 「更早」，而它明明只在一天前——滑动的 7 天与列表项相对时间退回日期的阈值一致。
 * 分组只作用于当前页（列表是分页的），跨页的组不合并，这由翻页语义自然承接。
 */
const DAY_MS = 86_400_000

const GROUP_LABELS = {
  today: '今天',
  yesterday: '昨天',
  week: '本周',
  earlier: '更早',
} as const

type GroupKey = keyof typeof GROUP_LABELS

function bucketOf(lastActiveAt: string): GroupKey {
  const then = Date.parse(lastActiveAt)
  if (Number.isNaN(then)) return 'earlier'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (then >= startOfToday) return 'today'
  if (then >= startOfToday - DAY_MS) return 'yesterday'
  if (then >= startOfToday - 7 * DAY_MS) return 'week'
  return 'earlier'
}

const groups = computed(() => {
  const buckets = new Map<GroupKey, AgentThreadSummaryDto[]>()
  for (const thread of props.threads) {
    const key = bucketOf(thread.last_active_at)
    const bucket = buckets.get(key)
    if (bucket) bucket.push(thread)
    else buckets.set(key, [thread])
  }
  return (Object.keys(GROUP_LABELS) as GroupKey[])
    .filter((key) => buckets.has(key))
    .map((key) => ({ key, label: GROUP_LABELS[key], threads: buckets.get(key)! }))
})
</script>

<template>
  <!-- nav 而不是 aside：这一列的作用是在会话之间导航，读屏用户按地标跳转时该能找到它。 -->
  <nav class="thread-sidebar" aria-label="会话记录" style="container-type: inline-size">
    <!-- 「新对话」不在这里：它是整页的主操作，唯一的一枚在外壳侧栏顶部。 -->
    <div class="sidebar-head">
      <h2 class="sidebar-title">
        会话记录
        <span v-if="total > 0" class="count">{{ total }}</span>
      </h2>
    </div>

    <p v-if="listState === 'loading'" class="sidebar-state" aria-live="polite">
      <BaseSpinner :size="14" />
      正在读取会话…
    </p>

    <BaseCallout
      v-else-if="listState === 'error'"
      class="sidebar-error"
      tone="danger"
      :title="listError?.title ?? '会话记录读取失败'"
      :description="listError?.description"
    >
      <template #icon><CircleAlert :size="15" aria-hidden="true" /></template>
      <template #actions>
        <BaseButton variant="outline" size="sm" class="retry" @click="emit('reload')">
          <template #icon><RotateCcw :size="14" aria-hidden="true" /></template>
          重试
        </BaseButton>
      </template>
    </BaseCallout>

    <!-- 空态说明「怎么产生第一条」，不只说「没有数据」：这一列在新账号上必然是空的，
         一句「暂无会话」只是重复了用户已经看到的事实。 -->
    <p v-else-if="isEmpty" class="sidebar-state">还没有会话。发出第一个问题就会在这里留下记录。</p>

    <div v-else class="thread-groups">
      <section v-for="group in groups" :key="group.key" class="thread-group">
        <h3 class="group-label">{{ group.label }}</h3>
        <ul class="thread-list">
          <ThreadListItem
            v-for="thread in group.threads"
            :key="thread.thread_id"
            :thread="thread"
            :active="thread.thread_id === activeThreadId"
            :deleting="deletingThreadIds.has(thread.thread_id)"
            @open="emit('open', thread.thread_id)"
            @remove="emit('remove', thread)"
          />
        </ul>
      </section>
    </div>

    <BasePager
      v-if="hasPrevious || hasMore"
      class="thread-pager"
      align="start"
      :has-previous="hasPrevious"
      :has-more="hasMore"
      @previous="emit('previousPage')"
      @next="emit('nextPage')"
    />
  </nav>
</template>

<style scoped>
.thread-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  /* 定位与滚动不归这里管：本组件挂在外壳侧栏的 #rail 容器里，
     那个容器负责吃掉剩余高度并自己滚动。 */
}

.sidebar-head {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  justify-content: space-between;
  padding-right: var(--space-1);
}

.sidebar-title {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  letter-spacing: 0;
}

.count {
  padding: 1px var(--space-1-5);
  border-radius: var(--radius-pill);
  color: var(--text-secondary);
  background: var(--surface-sunken);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.sidebar-state {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  padding: var(--space-1-5) var(--space-1);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.group-label {
  margin: 0 0 var(--space-0-5);
  padding: 0 var(--space-1);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.thread-group + .thread-group {
  margin-top: var(--space-2-5);
}

.thread-list {
  display: grid;
  gap: var(--space-0-5);
  margin: 0;
  padding: 0;
  list-style: none;
}

.sidebar-error {
  margin-top: var(--space-2-5);
}

.retry {
  margin-top: var(--space-2);
}

/* 侧栏里的分页器比表格里窄，只保留一点上间距；对齐与键距归 Pager。 */
.thread-pager {
  padding: var(--space-1) var(--space-1) 0;
}
</style>
