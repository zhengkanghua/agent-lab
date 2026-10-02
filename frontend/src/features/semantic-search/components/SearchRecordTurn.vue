<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, CircleAlert, RotateCcw, Search, SearchX } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { DocumentResult } from '../model/search-result'
import { isErrorRetryable, recordHitCount, type SearchRecord } from '../model/search-record'
import SearchResultCard from './SearchResultCard.vue'
import { scopeLabel } from '@/api/knowledge-scope'

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
  read: [result: DocumentResult, trigger: HTMLButtonElement | null]
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
  <article
    class="record"
    :class="[`is-${record.status}`, { 'is-collapsed': !expanded }]"
    style="container-type: inline-size"
  >
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
    <p v-if="record.scope" class="record-scope">{{ scopeLabel(record.scope) }}</p>

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

      <TransitionGroup
        v-else-if="record.status === 'success'"
        name="list"
        tag="div"
        class="result-list"
      >
        <SearchResultCard
          v-for="result in record.results"
          :key="result.documentId"
          :result="result"
          @read="(item, trigger) => emit('read', item, trigger)"
        />
      </TransitionGroup>
    </div>
  </article>
</template>

<style scoped>
.record {
  position: relative;
  padding-left: var(--space-8);
  background: transparent;
}

.record::before {
  content: '';
  position: absolute;
  top: 26px;
  bottom: -12px;
  left: 2.5px;
  width: 1px;
  background: var(--surface-sunken);
  border-radius: var(--radius-pill);
}

.record:last-child::before {
  bottom: 0;
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
  top: 19px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  color: transparent;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--surface-base);
  z-index: var(--z-local);
}

.record-mark svg {
  display: none;
}

.record-toggle {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 44px;
  padding: var(--space-2) var(--space-3-5);
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.record-toggle:hover:not(.record-toggle--static) {
  background: var(--surface-sunken);
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
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color var(--duration-fast) var(--ease-out-smooth);
}

/* 元信息（范围 · 命中数）靠右；内容长了要自己省略，不能顶到右边的折叠箭头上去。
   有 min-width: 0 只解决了「不肯缩」，还差 overflow 与省略号，缺了这两条长文本会溢出到框外。 */
.record-meta {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  white-space: nowrap;
  text-align: right;
  text-overflow: ellipsis;
}

.record-chevron {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  transition: transform var(--duration-fast) var(--ease-out-smooth);
}

.record-chevron.is-open {
  transform: rotate(180deg);
}

.record-body {
  padding: var(--space-1) 0 var(--space-6);
}

.record-scope {
  margin: 0 var(--space-3-5) var(--space-1-5);
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  overflow-wrap: anywhere;
}

.state-panel {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--space-3-5);
  align-items: center;
  padding: var(--space-8) var(--space-0-5) var(--space-2);
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
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
}

.state-panel p {
  margin-top: var(--space-1);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.6;
}

.retry-button {
  margin-top: var(--space-3-5);
}

.result-list {
  display: grid;
  gap: var(--space-2-5);
  padding-top: var(--space-4);
}

.skeleton-card {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-5);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-base);
}

/* 骨架条的皮肤（渐变、圆角、shimmer）在 motion.css 的共享 .skeleton-line 上，
   这里只声明这张卡的宽高变体。 */
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
  gap: var(--space-1-5);
  margin: var(--space-1) 0 0;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

@container (max-width: 600px) {
  .record {
    padding-left: var(--space-5);
  }

  .record-body {
    padding: var(--space-0-5) 0 var(--space-3-5);
  }

  .record-toggle {
    padding: var(--space-2) var(--space-2-5);
    gap: var(--space-2);
  }
}
</style>
