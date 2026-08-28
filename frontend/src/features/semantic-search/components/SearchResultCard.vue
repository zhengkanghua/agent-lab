<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpenText, ChevronDown, Clock3, ExternalLink, Layers3, Radar, Tag } from '@lucide/vue'
import {
  collapseExcerpt,
  formatAuthorLine,
  formatPublishedAt,
  formatRankLabel,
  formatScore,
  isExcerptLong,
  type NewsDocumentResult,
} from '../model/search-result'

const props = defineProps<{
  result: NewsDocumentResult
  rank: number
}>()

const emit = defineEmits<{
  read: [result: NewsDocumentResult, trigger: HTMLButtonElement | null]
}>()

const showAdditional = ref(false)
const bestExpanded = ref(false)
const rankLabel = computed(() => formatRankLabel(props.rank))
const bestIsLong = computed(() => isExcerptLong(props.result.bestMatch.excerpt))
const visibleBestExcerpt = computed(() =>
  collapseExcerpt(props.result.bestMatch.excerpt, bestExpanded.value),
)
const authorLine = computed(() => formatAuthorLine(props.result.authors))
const additionalRegionId = computed(() => `matches-${props.result.documentId}`)

function requestFullText(event: MouseEvent): void {
  emit('read', props.result, event.currentTarget as HTMLButtonElement | null)
}
</script>

<template>
  <article class="result-card">
    <div class="document-locator" aria-hidden="true">
      <strong>{{ rankLabel }}</strong>
      <span class="locator-line"></span>
      <span>
        <small>新闻</small>
        <b>{{ result.additionalMatches.length + 1 }} 段</b>
      </span>
    </div>

    <div class="result-main">
      <header class="result-header">
        <div class="result-meta">
          <span class="source-name">{{ result.sourceName }}</span>
          <span class="meta-item">
            <Clock3 :size="13" aria-hidden="true" />
            {{ formatPublishedAt(result.publishedAt) }}
          </span>
          <span v-if="authorLine" class="author-line">{{ authorLine }}</span>
        </div>

        <div
          class="score-block"
          :aria-label="`最高 Cosine 相关度分数 ${formatScore(result.bestScore)}，不是概率`"
          title="最高 Cosine 相关度分数，不是概率"
        >
          <Radar :size="15" aria-hidden="true" />
          <span>{{ formatScore(result.bestScore) }}</span>
        </div>
      </header>

      <h3 class="result-title">{{ result.title }}</h3>

      <section class="best-match" aria-label="最高分相关片段">
        <div class="match-heading">
          <span>最佳命中</span>
          <span>
            片段 {{ result.bestMatch.chunkIndex + 1 }} / {{ result.bestMatch.chunkCount }}
          </span>
        </div>
        <p class="result-excerpt" :class="{ 'is-collapsed': bestIsLong && !bestExpanded }">
          {{ visibleBestExcerpt }}
        </p>
        <button
          v-if="bestIsLong"
          class="text-button best-expand"
          type="button"
          :aria-expanded="bestExpanded"
          @click="bestExpanded = !bestExpanded"
        >
          {{ bestExpanded ? '收起最佳片段' : '展开最佳片段' }}
          <ChevronDown :size="15" :class="{ 'is-open': bestExpanded }" aria-hidden="true" />
        </button>
      </section>

      <button
        v-if="result.additionalMatches.length"
        class="related-toggle"
        type="button"
        :aria-expanded="showAdditional"
        :aria-controls="additionalRegionId"
        @click="showAdditional = !showAdditional"
      >
        <span>
          <Layers3 :size="15" aria-hidden="true" />
          {{
            showAdditional
              ? '收起相关片段'
              : `查看另外 ${result.additionalMatches.length} 个相关片段`
          }}
        </span>
        <ChevronDown :size="16" :class="{ 'is-open': showAdditional }" aria-hidden="true" />
      </button>

      <ol
        v-if="showAdditional && result.additionalMatches.length"
        :id="additionalRegionId"
        class="related-matches"
      >
        <li v-for="match in result.additionalMatches" :key="match.id">
          <div class="related-heading">
            <span>片段 {{ match.chunkIndex + 1 }} / {{ match.chunkCount }}</span>
            <span>{{ formatScore(match.score) }}</span>
          </div>
          <p>{{ match.excerpt }}</p>
        </li>
      </ol>

      <ul v-if="result.labels.length" class="label-list" aria-label="新闻标签">
        <li v-for="(label, index) in result.labels" :key="`${label}-${index}`">
          <Tag :size="12" aria-hidden="true" />
          {{ label }}
        </li>
      </ul>

      <footer class="result-actions">
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
.result-card {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr);
  gap: 18px;
  padding: 22px 22px 20px 18px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-md);
  background: var(--paper-50);
  box-shadow: 0 6px 20px rgba(24, 33, 31, 0.035);
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease,
    transform 150ms ease;
}

.result-card:hover {
  border-color: #b8c3bf;
  box-shadow: var(--shadow-soft);
  transform: translateY(-1px);
}

.document-locator {
  display: grid;
  grid-template-rows: auto minmax(20px, 1fr) auto;
  justify-items: center;
  align-self: stretch;
  min-height: 126px;
  padding: 2px 0;
  color: var(--signal-600);
  font-family: var(--mono-font);
}

.document-locator > strong {
  font-size: 0.88rem;
  letter-spacing: 0;
}

/* 几何属性见 styles/components/result-card.css；这里只给文档的强调色。 */
.locator-line {
  background: linear-gradient(var(--signal-500), var(--paper-300));
}

.document-locator > span:last-child {
  display: grid;
  justify-items: center;
  gap: 1px;
}

.document-locator small {
  color: var(--ink-500);
  font-family: var(--body-font);
  font-size: 0.62rem;
}

.document-locator b {
  color: var(--ink-700);
  font-size: 0.67rem;
  font-weight: 650;
  white-space: nowrap;
}

.result-main {
  min-width: 0;
}

.result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.result-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px 10px;
  min-width: 0;
  color: var(--ink-700);
  font-size: 0.73rem;
}

/* .source-name、.meta-item、.author-line 见 styles/components/result-card.css。 */

/* 其余属性见 styles/components/result-card.css；这里只给文档的强调色。 */
.score-block {
  color: var(--signal-600);
}

.result-title {
  margin-top: 12px;
  overflow-wrap: anywhere;
  color: var(--ink-950);
  font-family: var(--display-font);
  font-size: 1.28rem;
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.36;
}

.best-match {
  margin-top: 14px;
}

/* .match-heading 见 styles/components/result-card.css；这里只留同组的 .related-heading。 */
.related-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: var(--ink-500);
  font-family: var(--mono-font);
  font-size: 0.66rem;
}

.result-excerpt,
.related-matches p {
  max-width: 82ch;
  overflow-wrap: anywhere;
  color: var(--ink-800);
  font-size: 0.92rem;
  line-height: 1.76;
  white-space: pre-line;
}

.result-excerpt {
  margin-top: 8px;
}

.result-excerpt.is-collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  overflow: hidden;
}

.text-button,
.related-toggle {
  border: 0;
  color: var(--signal-600);
  background: transparent;
  font-size: 0.76rem;
  font-weight: 720;
}

.text-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 32px;
  padding: 3px 0;
}

.text-button svg,
.related-toggle > svg {
  transition: transform 150ms ease;
}

.text-button svg.is-open,
.related-toggle > svg.is-open {
  transform: rotate(180deg);
}

.related-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 42px;
  margin-top: 13px;
  padding: 8px 0;
  border-top: 1px solid var(--paper-300);
  border-bottom: 1px solid var(--paper-300);
  text-align: left;
}

.related-toggle span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.related-matches {
  padding: 0;
  margin: 0;
  list-style: none;
}

.related-matches li {
  padding: 15px 0 16px;
  border-bottom: 1px solid var(--paper-200);
}

.related-matches p {
  margin-top: 7px;
  font-size: 0.86rem;
}

.related-heading span:last-child {
  flex: 0 0 auto;
  color: var(--signal-600);
}

/* .label-list 见 styles/components/result-card.css。 */

.result-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 17px;
  padding-top: 15px;
  border-top: 1px solid var(--paper-200);
}

/* .read-button 见 styles/components/result-card.css；这里只留同组的链接样式。 */
.result-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  color: var(--ink-800);
  background: var(--paper-50);
  font-size: 0.76rem;
  font-weight: 740;
  text-decoration: none;
}

.result-actions a:hover {
  border-color: var(--source-500);
  color: var(--source-600);
}

@media (max-width: 680px) {
  .result-card {
    grid-template-columns: 38px minmax(0, 1fr);
    gap: 12px;
    padding: 18px 14px 17px 11px;
  }

  .document-locator {
    min-height: 110px;
  }

  .result-header {
    align-items: flex-start;
    flex-direction: column-reverse;
    gap: 9px;
  }

  /* .score-block、.match-heading、.read-button 的窄屏覆盖见
     styles/components/result-card.css。 */

  .result-title {
    margin-top: 10px;
    font-size: 1.12rem;
  }

  .result-excerpt,
  .related-matches p {
    font-size: 0.88rem;
  }

  .result-actions {
    align-items: stretch;
  }

  .result-actions a {
    flex: 1 1 132px;
  }
}
</style>
