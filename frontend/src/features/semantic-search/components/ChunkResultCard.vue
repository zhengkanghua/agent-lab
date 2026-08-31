<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpenText, ChevronDown, Clock3, ExternalLink, Radar, Tag } from '@lucide/vue'
import {
  collapseExcerpt,
  formatAuthorLine,
  formatPublishedAt,
  formatRankLabel,
  formatScore,
  isExcerptLong,
  type NewsChunkResult,
} from '../model/search-result'

const props = defineProps<{
  result: NewsChunkResult
  rank: number
}>()

const emit = defineEmits<{
  read: [result: NewsChunkResult, trigger: HTMLButtonElement | null]
}>()

const expanded = ref(false)
const rankLabel = computed(() => formatRankLabel(props.rank))
const excerptIsLong = computed(() => isExcerptLong(props.result.excerpt))
const visibleExcerpt = computed(() => collapseExcerpt(props.result.excerpt, expanded.value))
const authorLine = computed(() => formatAuthorLine(props.result.authors))

function requestFullText(event: MouseEvent): void {
  emit('read', props.result, event.currentTarget as HTMLButtonElement | null)
}
</script>

<template>
  <article class="chunk-card">
    <div class="chunk-locator" aria-hidden="true">
      <strong>{{ rankLabel }}</strong>
      <span class="locator-line"></span>
      <span>
        <small>片段</small>
        <b>{{ result.chunkIndex + 1 }} / {{ result.chunkCount }}</b>
      </span>
    </div>

    <div class="chunk-main">
      <header class="chunk-header">
        <div class="chunk-meta">
          <span class="source-name">{{ result.sourceName }}</span>
          <span class="meta-item">
            <Clock3 :size="13" aria-hidden="true" />
            {{ formatPublishedAt(result.publishedAt) }}
          </span>
          <span v-if="authorLine" class="author-line">{{ authorLine }}</span>
        </div>

        <div
          class="score-block"
          :aria-label="`Cosine 相关度分数 ${formatScore(result.score)}，不是概率`"
          title="原始 Cosine 相关度分数，不是概率"
        >
          <Radar :size="15" aria-hidden="true" />
          <span>{{ formatScore(result.score) }}</span>
        </div>
      </header>

      <h3 class="chunk-title">{{ result.title }}</h3>

      <section class="chunk-match" aria-label="语义命中片段">
        <div class="match-heading">
          <span>语义命中</span>
          <span>片段 {{ result.chunkIndex + 1 }} / {{ result.chunkCount }}</span>
        </div>
        <p class="chunk-excerpt" :class="{ 'is-collapsed': excerptIsLong && !expanded }">
          {{ visibleExcerpt }}
        </p>
        <button
          v-if="excerptIsLong"
          class="expand-button"
          type="button"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起片段' : '展开片段' }}
          <ChevronDown :size="15" :class="{ 'is-open': expanded }" aria-hidden="true" />
        </button>
      </section>

      <ul v-if="result.labels.length" class="label-list" aria-label="新闻标签">
        <li v-for="(label, index) in result.labels" :key="`${label}-${index}`">
          <Tag :size="12" aria-hidden="true" />
          {{ label }}
        </li>
      </ul>

      <footer class="chunk-actions">
        <button class="read-button" type="button" @click="requestFullText">
          <BookOpenText :size="16" aria-hidden="true" />
          阅读全文
        </button>
        <a :href="result.url" target="_blank" rel="noopener noreferrer">
          <ExternalLink :size="15" aria-hidden="true" />
          访问原文
        </a>
      </footer>
    </div>
  </article>
</template>

<style scoped>
.chunk-card {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr);
  gap: 18px;
  padding: 22px 22px 20px 18px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--surface-raised);
  box-shadow: var(--shadow-card);
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease,
    transform 150ms ease;
}

.chunk-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-soft);
  transform: translateY(-1px);
}

.chunk-locator {
  display: grid;
  grid-template-rows: auto minmax(20px, 1fr) auto;
  justify-items: center;
  align-self: stretch;
  min-height: 126px;
  padding: 2px 0;
  color: var(--accent);
  font-family: var(--mono-font);
}

.chunk-locator > strong {
  font-size: 0.88rem;
}

/* 几何属性见 styles/components/result-card.css；这里只给 Chunk 的强调色。 */
.locator-line {
  background: linear-gradient(var(--accent), var(--border-subtle));
}

.chunk-locator > span:last-child {
  display: grid;
  justify-items: center;
  gap: 1px;
}

.chunk-locator small {
  color: var(--text-muted);
  font-family: var(--body-font);
  font-size: 0.62rem;
}

.chunk-locator b {
  color: var(--text-secondary);
  font-size: 0.67rem;
  font-weight: 650;
  white-space: nowrap;
}

.chunk-main {
  min-width: 0;
}

.chunk-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.chunk-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 10px;
  min-width: 0;
  color: var(--text-secondary);
  font-size: 0.73rem;
}

/* .source-name、.meta-item、.author-line 见 styles/components/result-card.css。 */

/* 其余属性见 styles/components/result-card.css；这里只给 Chunk 的强调色。 */
.score-block {
  color: var(--accent);
}

.chunk-title {
  margin-top: 12px;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: 1.28rem;
  font-weight: 780;
  line-height: 1.36;
}

.chunk-match {
  margin-top: 14px;
}

/* .match-heading 见 styles/components/result-card.css。 */

.chunk-excerpt {
  max-width: 82ch;
  margin-top: 8px;
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: 0.92rem;
  line-height: 1.76;
  white-space: pre-line;
}

.chunk-excerpt.is-collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  overflow: hidden;
}

.expand-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 32px;
  padding: 3px 0;
  border: 0;
  color: var(--accent);
  background: transparent;
  font-size: 0.76rem;
  font-weight: 720;
}

.expand-button svg {
  transition: transform 150ms ease;
}

.expand-button svg.is-open {
  transform: rotate(180deg);
}

/* .label-list 见 styles/components/result-card.css。 */

.chunk-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 17px;
  padding-top: 15px;
  border-top: 1px solid var(--surface-sunken);
}

/* .read-button 见 styles/components/result-card.css；这里只留同组的链接样式。 */
.chunk-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.76rem;
  font-weight: 740;
  text-decoration: none;
}

.chunk-actions a:hover {
  border-color: var(--accent);
  color: var(--accent);
}

@media (max-width: 680px) {
  .chunk-card {
    grid-template-columns: 38px minmax(0, 1fr);
    gap: 12px;
    padding: 18px 14px 17px 11px;
  }

  .chunk-locator {
    min-height: 110px;
  }

  .chunk-header {
    align-items: flex-start;
    flex-direction: column-reverse;
    gap: 9px;
  }

  /* .score-block、.match-heading、.read-button 的窄屏覆盖见
     styles/components/result-card.css。 */

  .chunk-title {
    margin-top: 10px;
    font-size: 1.12rem;
  }

  .chunk-excerpt {
    font-size: 0.88rem;
  }

  .chunk-actions {
    align-items: stretch;
  }

  .chunk-actions a {
    flex: 1 1 132px;
  }
}
</style>
