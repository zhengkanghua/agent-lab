<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUpRight, CircleAlert, RotateCcw, Search, SearchX } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { ApiError } from '@/api/client'
import type { SearchStatus } from '../model/search-validation'
import { presentSearchError } from '../model/search-error'
import type {
  NewsChunkResult,
  NewsDocumentResult,
  NewsReadableResult,
  SearchMode,
} from '../model/search-result'
import { SEARCH_EXAMPLES } from '../constants/examples'
import ChunkResultCard from './ChunkResultCard.vue'
import SearchResultCard from './SearchResultCard.vue'

const props = defineProps<{
  mode: SearchMode
  status: SearchStatus
  results: NewsDocumentResult[]
  chunkResults: NewsChunkResult[]
  error: ApiError | null
  lastQuery: string
}>()

const emit = defineEmits<{
  retry: []
  chooseExample: [value: string]
  read: [result: NewsReadableResult, trigger: HTMLButtonElement | null]
}>()

const errorPresentation = computed(() => (props.error ? presentSearchError(props.error) : null))
const resultCount = computed(() =>
  props.mode === 'document' ? props.results.length : props.chunkResults.length,
)
const isDocumentMode = computed(() => props.mode === 'document')
</script>

<template>
  <section class="results-section" aria-labelledby="results-title">
    <header class="results-heading">
      <div class="results-status" aria-live="polite" aria-atomic="true">
        <p class="section-label">检索结果</p>
        <h2 id="results-title">
          <template v-if="status === 'success'">
            {{
              isDocumentMode ? `找到 ${resultCount} 篇相关新闻` : `找到 ${resultCount} 个相关片段`
            }}
          </template>
          <template v-else-if="status === 'empty'">
            {{ isDocumentMode ? '没有找到相关新闻' : '没有找到相关片段' }}
          </template>
          <template v-else-if="status === 'loading'">
            {{ isDocumentMode ? '正在查找相关新闻' : '正在查找相关片段' }}
          </template>
          <template v-else-if="status === 'error'">本次搜索未完成</template>
          <template v-else>等待搜索</template>
        </h2>
        <p v-if="lastQuery" class="results-query">“{{ lastQuery }}”</p>
      </div>

      <p v-if="status === 'success'" class="score-note">
        {{
          isDocumentMode
            ? '按每篇最高相关度排序，分数不是概率'
            : '保留原始 Chunk 排序，分数不是概率'
        }}
      </p>
    </header>

    <div v-if="status === 'idle'" class="state-panel idle-state">
      <div class="state-copy">
        <span class="state-icon"><Search :size="22" aria-hidden="true" /></span>
        <div>
          <h3>从一个研究问题开始</h3>
          <p>
            {{
              isDocumentMode
                ? '按新闻汇总相关证据，也可以直接使用示例。'
                : '逐条查看原始 Chunk 命中，也可以直接使用示例。'
            }}
          </p>
        </div>
      </div>
      <div class="example-list" aria-label="示例检索">
        <button
          v-for="example in SEARCH_EXAMPLES"
          :key="example"
          type="button"
          @click="emit('chooseExample', example)"
        >
          <span>{{ example }}</span>
          <ArrowUpRight :size="16" aria-hidden="true" />
        </button>
      </div>
    </div>

    <div v-else-if="status === 'loading'" class="result-list" aria-busy="true">
      <p class="sr-only" role="status">
        {{
          isDocumentMode
            ? '正在从新闻索引中读取文档分组结果'
            : '正在从新闻索引中读取原始 Chunk 结果'
        }}
      </p>
      <div v-for="index in 3" :key="index" class="skeleton-card" aria-hidden="true">
        <span class="skeleton-locator"></span>
        <div class="skeleton-copy">
          <span class="skeleton-line skeleton-line--meta"></span>
          <span class="skeleton-line skeleton-line--title"></span>
          <span class="skeleton-line"></span>
          <span class="skeleton-line skeleton-line--short"></span>
        </div>
        <span class="skeleton-score"></span>
      </div>
      <p class="loading-caption"><BaseSpinner :size="15" /> 正在联系语义检索服务</p>
    </div>

    <div v-else-if="status === 'empty'" class="state-panel empty-state">
      <div class="state-copy">
        <span class="state-icon"><SearchX :size="22" aria-hidden="true" /></span>
        <div>
          <h3>换一种表达再试</h3>
          <p>使用更具体的事件、机构或时间范围，通常能得到更准确的结果。</p>
        </div>
      </div>
    </div>

    <div
      v-else-if="status === 'error' && errorPresentation"
      class="state-panel error-state"
      role="alert"
    >
      <div class="state-copy">
        <span class="state-icon state-icon--error">
          <CircleAlert :size="22" aria-hidden="true" />
        </span>
        <div>
          <h3>{{ errorPresentation.title }}</h3>
          <p>{{ errorPresentation.description }}</p>
          <BaseButton
            v-if="errorPresentation.retryable"
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
    </div>

    <div
      v-else-if="status === 'success'"
      class="result-list"
      :aria-label="isDocumentMode ? '相关新闻' : '相关 Chunk 片段'"
    >
      <template v-if="isDocumentMode">
        <SearchResultCard
          v-for="(result, index) in results"
          :key="result.documentId"
          :result="result"
          :rank="index"
          @read="(item, trigger) => emit('read', item, trigger)"
        />
      </template>
      <template v-else>
        <ChunkResultCard
          v-for="(result, index) in chunkResults"
          :key="`${result.id}-${index}`"
          :result="result"
          :rank="index"
          @read="(item, trigger) => emit('read', item, trigger)"
        />
      </template>
    </div>
  </section>
</template>

<style scoped>
.results-section {
  min-width: 0;
}

.results-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  min-height: 82px;
  padding: 5px 4px 16px;
  border-bottom: 1px solid var(--text-primary);
}

.results-status {
  min-width: 0;
}

.section-label {
  color: var(--accent);
  font-size: 0.72rem;
  font-weight: 760;
  letter-spacing: 0;
}

h2 {
  margin-top: 5px;
  color: var(--text-primary);
  font-size: 1.55rem;
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.25;
}

.results-query {
  max-width: 680px;
  margin-top: 6px;
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: 0.78rem;
  line-height: 1.45;
}

.score-note {
  flex: 0 0 auto;
  max-width: 240px;
  color: var(--text-muted);
  font-size: 0.7rem;
  text-align: right;
}

.result-list {
  display: grid;
  gap: 11px;
  padding-top: 16px;
}

.state-panel {
  min-height: 300px;
  padding: 38px 4px;
  border-bottom: 1px solid var(--border-subtle);
}

.state-copy {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 15px;
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
  font-size: 1.12rem;
  font-weight: 760;
  letter-spacing: 0;
}

.state-panel p {
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 0.84rem;
  line-height: 1.6;
}

.example-list {
  display: grid;
  max-width: 620px;
  margin-top: 26px;
  border-top: 1px solid var(--border-subtle);
}

.example-list button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 49px;
  padding: 8px 5px;
  border: 0;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  background: transparent;
  font-size: 0.82rem;
  text-align: left;
  transition:
    color 150ms ease,
    padding 150ms ease,
    background-color 150ms ease;
}

.example-list button:hover {
  padding-right: 10px;
  padding-left: 10px;
  color: var(--accent);
  background: var(--surface-raised);
}

/* 描边与尺寸归 BaseButton 的 outline + sm，这里只留与错误正文的间距。 */
.retry-button {
  margin-top: 16px;
}

.skeleton-card {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) 84px;
  gap: 18px;
  min-height: 226px;
  padding: 23px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
}

.skeleton-locator,
.skeleton-score,
.skeleton-line {
  display: block;
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

.skeleton-locator {
  width: 28px;
  height: 96px;
}

.skeleton-copy {
  display: grid;
  align-content: start;
  gap: 13px;
}

.skeleton-line {
  width: 94%;
  height: 13px;
}

.skeleton-line--meta {
  width: 27%;
  height: 9px;
}

.skeleton-line--title {
  width: 74%;
  height: 21px;
}

.skeleton-line--short {
  width: 61%;
}

.skeleton-score {
  width: 68px;
  height: 38px;
  justify-self: end;
}

.loading-caption {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 3px 0 0 61px;
  color: var(--text-muted);
  font-size: 0.7rem;
}

/* 原 @keyframes skeleton-shimmer 与 DocumentReader 的 shimmer 逐字节相同，
   已合并为 styles/components/motion.css 里的 shimmer。 */

@media (max-width: 680px) {
  .results-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 9px;
    min-height: 0;
    padding-bottom: 13px;
  }

  .score-note {
    max-width: none;
    text-align: left;
  }

  .state-panel {
    min-height: 240px;
    padding-top: 30px;
  }

  .skeleton-card {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 12px;
    min-height: 205px;
    padding: 18px 15px;
  }

  .skeleton-score {
    display: none;
  }

  .loading-caption {
    margin-left: 40px;
  }
}
</style>
