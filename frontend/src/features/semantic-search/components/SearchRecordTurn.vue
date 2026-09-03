<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, CircleAlert, RotateCcw, Search, SearchX } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { NewsDocumentResult } from '../model/search-result'
import { isErrorRetryable, recordHitCount, type SearchRecord } from '../model/search-record'
import SearchResultCard from './SearchResultCard.vue'

/* 检索流里的一条检索记录（Q5 乙 / Q8 / Q9 / Q10 甲）。
 *
 * 模型二是「最新一条贴在最靠近输入框的位置」，旧记录往下沉。本组件负责单条记录怎么呈现：
 *  - 展开态：一条头（检索词 + 命中概览）+ 内容。内容是 loading 面板 / 空态 / 错误 / 结果卡列表；
 *  - 折叠态：压成一行「检索词 + 命中数」的标题行，点开回看（Q10 甲：检索词 + 命中数做识别信息）。
 *
 * 展开与否由父级控制（latest 恒展开、旧记录默认折叠、手动展开的保留），本组件只转发 toggle。
 */

const props = defineProps<{
  record: SearchRecord
  /** 是否为最新一条记录：是则不渲染折叠控件（最新轮恒展开）。 */
  isLatest: boolean
  /** 当前展开态（由父级统一维护，本组件不自己存）。 */
  expanded: boolean
}>()

const emit = defineEmits<{
  toggle: []
  retry: []
  read: [result: NewsDocumentResult, trigger: HTMLButtonElement | null]
}>()

const hitCount = computed(() => recordHitCount(props.record))
const statusMeta = computed(() => {
  if (props.record.status === 'success') return `命中 ${hitCount.value} 篇`
  if (props.record.status === 'empty') return '没有命中'
  if (props.record.status === 'error') return '本次未完成'
  return '检索中…'
})
const canRetry = computed(() => isErrorRetryable(props.record))

function toggle(): void {
  emit('toggle')
}
</script>

<template>
  <article class="record" :class="[`is-${record.status}`, { 'is-collapsed': !expanded }]" style="container-type: inline-size">
    <!-- 头：检索词是这条记录的识别主信息。展开态也放，让每条记录自带归属；latest 不提供折叠。 -->
    <header class="record-head" :aria-expanded="expanded ? 'true' : 'false'">
      <span class="record-mark" aria-hidden="true"><Search :size="15" /></span>

      <button
        v-if="!isLatest"
        type="button"
        class="record-toggle"
        :aria-expanded="expanded"
        @click="toggle"
      >
        <span class="record-query">{{ record.query }}</span>
        <span class="record-meta">{{ statusMeta }}</span>
        <ChevronDown
          class="record-chevron"
          :class="{ 'is-open': expanded }"
          :size="16"
          aria-hidden="true"
        />
      </button>

      <!-- latest 无折叠控件，头只是静态信息。 -->
      <div v-else class="record-toggle record-toggle--static">
        <span class="record-query">{{ record.query }}</span>
        <span class="record-meta">{{ statusMeta }}</span>
      </div>
    </header>

    <!-- 展开内容。collapsed 用 v-if 整块去掉，不留 aria-hidden 的空标签。 -->
    <div v-if="expanded" class="record-body">
      <div
        v-if="record.status === 'loading'"
        class="state-panel"
        aria-live="polite"
        aria-busy="true"
      >
        <p class="sr-only">正在检索「{{ record.query }}」</p>
        <div v-for="index in 3" :key="index" class="skeleton-card" aria-hidden="true">
          <span class="skeleton-line skeleton-line--title"></span>
          <span class="skeleton-line"></span>
          <span class="skeleton-line skeleton-line--short"></span>
        </div>
        <p class="loading-caption"><BaseSpinner :size="15" /> 正在联系语义检索服务</p>
      </div>

      <div v-else-if="record.status === 'empty'" class="state-panel empty-state">
        <span class="state-icon"><SearchX :size="22" aria-hidden="true" /></span>
        <div>
          <h3>换一种表达再试</h3>
          <p>使用更具体的事件、机构或时间范围，通常能得到更准确的结果。</p>
        </div>
      </div>

      <div
        v-else-if="record.status === 'error' && record.error"
        class="state-panel error-state"
        role="alert"
      >
        <span class="state-icon state-icon--error"
          ><CircleAlert :size="22" aria-hidden="true"
        /></span>
        <div>
          <h3>{{ record.error.title }}</h3>
          <p>{{ record.error.description }}</p>
          <BaseButton
            v-if="canRetry"
            class="retry-button"
            variant="outline"
            size="sm"
            @click="emit('retry')"
          >
            <template #icon><RotateCcw :size="15" aria-hidden="true" /></template>
            再试一次
          </BaseButton>
        </div>
      </div>

      <TransitionGroup v-else-if="record.status === 'success'" name="list" tag="div" class="result-list">
        <SearchResultCard
          v-for="(result, index) in record.results"
          :key="result.documentId"
          :result="result"
          :rank="index"
          @read="(item, trigger) => emit('read', item, trigger)"
        />
      </TransitionGroup>
    </div>
  </article>
</template>

<style scoped>
.record {
  position: relative;
  padding-left: 32px;
  background: transparent;
  transition: opacity 200ms ease;
}

.record::before {
  content: '';
  position: absolute;
  top: 26px;
  bottom: -12px;
  left: 6px;
  width: 2px;
  background: var(--surface-sunken);
  border-radius: 1px;
}

.record:last-child::before {
  bottom: 0;
}

/* 折叠态记录更沉静：弱化透明度等。 */
.record.is-collapsed {
  opacity: 0.8;
}

.record.is-error::before {
  background: var(--danger-soft);
}

.record-head {
  display: flex;
  align-items: center;
  min-height: 44px;
}

.record-mark {
  position: absolute;
  left: 0;
  top: 14px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  color: transparent;
  background: var(--accent);
  box-shadow: 0 0 0 4px var(--surface-base);
  z-index: 1;
}

.record-mark svg {
  display: none;
}

.record-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 52px;
  padding: 8px 14px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.record-toggle--static {
  cursor: default;
}

.record-toggle:hover:not(.record-toggle--static) .record-query {
  color: var(--accent);
}

.record-query {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 0.95rem;
  font-weight: 760;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 150ms ease;
}

.record-meta {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-muted);
  font-size: 0.72rem;
  white-space: nowrap;
  text-align: right;
}

.record-chevron {
  flex: 0 0 auto;
  color: var(--text-muted);
  transition: transform 150ms ease;
}

.record-chevron.is-open {
  transform: rotate(180deg);
}

.record-body {
  padding: 4px 0 24px;
}

.state-panel {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 15px;
  align-items: center;
  padding: 30px 2px 8px;
}

.state-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  color: var(--accent);
  background: var(--accent-soft);
}

.state-icon--error {
  color: var(--danger);
  background: var(--danger-soft);
}

.state-panel h3 {
  color: var(--text-primary);
  font-size: 1.06rem;
  font-weight: 760;
}

.state-panel p {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 0.84rem;
  line-height: 1.6;
}

.retry-button {
  margin-top: 14px;
}

.result-list {
  display: grid;
  gap: 11px;
  padding-top: 16px;
}

.skeleton-card {
  display: grid;
  gap: 12px;
  padding: 22px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-base);
}

.skeleton-line {
  display: block;
  width: 94%;
  height: 13px;
  border-radius: 3px;
  background: linear-gradient(
    90deg,
    var(--surface-sunken),
    var(--surface-raised),
    var(--surface-sunken)
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.skeleton-line--title {
  width: 62%;
  height: 21px;
}

.skeleton-line--short {
  width: 56%;
}

.loading-caption {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 0.7rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

@container (max-width: 600px) {
  .record {
    padding-left: 20px;
  }
  .record::before {
    left: 4px;
  }
  .record-mark {
    width: 10px;
    height: 10px;
    top: 16px;
  }

  .record-body {
    padding: 2px 0 14px;
  }

  .record-toggle {
    padding: 8px 10px;
    gap: 8px;
  }
}
</style>
