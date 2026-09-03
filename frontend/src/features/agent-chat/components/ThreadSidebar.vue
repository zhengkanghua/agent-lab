<script setup lang="ts">
import { CircleAlert, MessageSquarePlus, RotateCcw } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'
import type { AgentErrorPresentation } from '../model/agent-error'
import type { ThreadListState } from '../composables/useThreadList'
import ThreadListItem from './ThreadListItem.vue'

defineProps<{
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
  newConversation: []
}>()
</script>

<template>
  <!-- nav 而不是 aside：这一列的作用是在会话之间导航，读屏用户按地标跳转时该能找到它。 -->
  <nav class="thread-sidebar" aria-label="会话记录" style="container-type: inline-size">
    <div class="sidebar-head">
      <h2 class="sidebar-title">
        会话记录
        <span v-if="total > 0" class="count">{{ total }}</span>
      </h2>
      <BaseButton variant="outline" size="sm" @click="emit('newConversation')">
        <template #icon><MessageSquarePlus :size="15" aria-hidden="true" /></template>
        新对话
      </BaseButton>
    </div>

    <p v-if="listState === 'loading'" class="sidebar-state" aria-live="polite">
      <BaseSpinner :size="14" />
      正在读取会话…
    </p>

    <div v-else-if="listState === 'error'" class="sidebar-error" role="alert">
      <p class="error-title">
        <CircleAlert :size="15" aria-hidden="true" />
        {{ listError?.title ?? '会话记录读取失败' }}
      </p>
      <p class="error-description">{{ listError?.description }}</p>
      <BaseButton variant="outline" size="sm" class="retry" @click="emit('reload')">
        <template #icon><RotateCcw :size="14" aria-hidden="true" /></template>
        重试
      </BaseButton>
    </div>

    <!-- 空态说明「怎么产生第一条」，不只说「没有数据」：这一列在新账号上必然是空的，
         一句「暂无会话」只是重复了用户已经看到的事实。 -->
    <p v-else-if="isEmpty" class="sidebar-state">还没有会话。发出第一个问题就会在这里留下记录。</p>

    <TransitionGroup v-else name="list" tag="ul" class="thread-list">
      <ThreadListItem
        v-for="thread in threads"
        :key="thread.thread_id"
        :thread="thread"
        :active="thread.thread_id === activeThreadId"
        :deleting="deletingThreadIds.has(thread.thread_id)"
        @open="emit('open', thread.thread_id)"
        @remove="emit('remove', thread)"
      />
    </TransitionGroup>

    <div v-if="hasPrevious || hasMore" class="pager">
      <BaseButton
        variant="outline"
        size="sm"
        :disabled="!hasPrevious"
        @click="emit('previousPage')"
      >
        上一页
      </BaseButton>
      <BaseButton variant="outline" size="sm" :disabled="!hasMore" @click="emit('nextPage')">
        下一页
      </BaseButton>
    </div>
  </nav>
</template>

<style scoped>
.thread-sidebar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /* sticky 而不是固定高度加内部滚动：会话列表一页最多 20 条，装得下；
     内部滚动会在页面本身也能滚的时候产生两条滚动条，鼠标停在哪决定滚哪个，很难用。 */
  position: sticky;
  top: calc(var(--app-topbar-height, 69px) + 18px);
  padding: 14px 8px 14px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
}

.sidebar-head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding-right: 4px;
}

.sidebar-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-tertiary);
  font-size: 0.7rem;
  font-weight: 720;
  letter-spacing: 0.04em;
}

.count {
  padding: 1px 6px;
  border-radius: 999px;
  color: var(--text-secondary);
  background: var(--surface-sunken);
  font-size: 0.68rem;
  font-weight: 650;
}

.sidebar-state {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 4px;
  color: var(--text-tertiary);
  font-size: 0.78rem;
  line-height: 1.6;
}

.thread-list {
  display: grid;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sidebar-error {
  padding: 10px 11px;
  border: 1px solid var(--danger-soft);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
}

.error-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--danger);
  font-size: 0.78rem;
  font-weight: 720;
}

.error-description {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 0.74rem;
  line-height: 1.55;
}

.retry {
  margin-top: 9px;
}

.pager {
  display: flex;
  gap: 8px;
  padding: 4px 4px 0;
}

/* 窄屏把它变成正常流里的一块，不再 sticky：一列 20 条会话钉在顶上会把对话挤出视口。 */
@container (max-width: 900px) {
  .thread-sidebar {
    position: static;
  }
}
</style>
