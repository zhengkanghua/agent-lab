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
  <article class="result-card" style="container-type: inline-size">
    <div class="document-locator" aria-hidden="true">
      <strong>{{ rankLabel }}</strong>
      <span class="locator-line"></span>
      <span>
        <small>文档</small>
        <b>{{ result.additionalMatches.length + 1 }} 段</b>
      </span>
    </div>

    <div class="result-main">
      <header class="result-header">
        <div class="result-meta">
          <span v-if="result.knowledgeBaseName" class="source-name knowledge-base-name">{{
            result.knowledgeBaseName
          }}</span>
          <span class="source-name">{{
            result.sourceName ?? result.uploadFilename ?? '未指定来源'
          }}</span>
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

      <ul v-if="result.labels.length" class="label-list" aria-label="文档标签">
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
        <a v-if="result.url" :href="result.url" target="_blank" rel="noopener noreferrer">
          <ExternalLink :size="15" aria-hidden="true" />
          访问原文
        </a>
        <span v-else class="origin-missing">未提供原文链接</span>
      </footer>
    </div>
  </article>
</template>

<style scoped>
/* 全部样式收在组件内：styles/components/result-card.css 原是「按片段」卡片与本文
   件共享的层，重构去掉按片段后只剩这一个使用方，共享层反而多了一跳。折叠（消融）
   回组件，跳转与文件数各少一份。 */
.result-card {
  display: grid;
  grid-template-columns: 50px minmax(0, 1fr);
  gap: 18px;
  padding: 22px 0;
  border: none;
  border-bottom: 1px solid var(--surface-sunken);
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.result-card:last-child {
  border-bottom: none;
}

.document-locator {
  display: grid;
  grid-template-rows: auto minmax(20px, 1fr) auto;
  justify-items: center;
  align-self: stretch;
  min-height: 126px;
  padding: 2px 0;
  color: var(--text-secondary);
  font-family: var(--mono-font);
}

.document-locator > strong {
  font-size: var(--fs-sm);
  letter-spacing: 0;
}

.locator-line {
  width: 1px;
  min-height: 24px;
  margin: 8px 0;
  background: linear-gradient(var(--accent), var(--border-subtle));
}

.document-locator > span:last-child {
  display: grid;
  justify-items: center;
  gap: 1px;
}

.document-locator small {
  color: var(--text-tertiary);
  font-family: var(--body-font);
  font-size: var(--fs-xs);
}

.document-locator b {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
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
  max-width: 100%;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.source-name {
  max-width: min(260px, 100%);
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-base-name {
  padding: 3px 7px;
  border-radius: var(--radius-sm);
  color: var(--accent-hover);
  background: var(--accent-soft);
  font-weight: var(--fw-bold);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.author-line {
  max-width: min(240px, 100%);
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score-block {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 6px;
  min-width: 76px;
  min-height: 32px;
  padding: 5px 8px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-base);
  font-family: var(--mono-font);
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
}

.result-title {
  margin-top: 10px;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: var(--fs-lg);
  font-weight: var(--fw-bold);
  letter-spacing: 0;
  line-height: 1.36;
}

.best-match {
  margin-top: 12px;
}

/* 「匹配位置」标题自己的颜色，与卡片强调色无关。 */
.match-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
}

.match-heading span:first-child {
  color: var(--accent-hover);
  font-family: var(--body-font);
  font-weight: var(--fw-bold);
}

.related-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
}

.related-heading span:last-child {
  flex: 0 0 auto;
  color: var(--text-secondary);
}

.result-excerpt,
.related-matches p {
  /* 行长上限用 em 不用 ch：ch 是「0」的宽，量全角正文会偏窄一截。65em ≈ 65 个
     全角字符，在 --stream-width 列宽下正好是不再收紧的自然上限（ADR 0016）。 */
  max-width: 65em;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: var(--fs-sm);
  line-height: 1.68;
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
  color: var(--accent);
  background: transparent;
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out-smooth);
}

.text-button:hover,
.related-toggle:hover {
  color: var(--accent-hover);
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
  transition: transform var(--duration-normal) var(--ease-out-smooth);
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
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
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
  border-bottom: 1px solid var(--surface-sunken);
}

.related-matches p {
  margin-top: 7px;
  font-size: var(--fs-sm);
}

.label-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0;
  margin: 15px 0 0;
  list-style: none;
}

.label-list li {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 3px 7px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  background: var(--surface-base);
  font-size: var(--fs-xs);
}

.result-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--surface-sunken);
}

.read-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--surface-raised);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  text-decoration: none;
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

.read-button:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  transform: translateY(-1px);
}

.read-button:active {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.result-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  text-decoration: none;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-out-smooth);
}

.result-actions a:hover {
  background: var(--surface-hover);
  color: var(--accent);
  transform: translateY(-1px);
}

.result-actions a:active {
  transform: scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

@media (prefers-reduced-motion: reduce) {
  .read-button,
  .result-actions a {
    transition-property: background-color, border-color, color;
  }

  .read-button:hover,
  .read-button:active,
  .result-actions a:hover,
  .result-actions a:active {
    transform: none;
  }
}

.origin-missing {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

@container (max-width: 680px) {
  .result-card {
    grid-template-columns: 38px minmax(0, 1fr);
    gap: 12px;
    padding: 24px 0 20px;
  }

  .document-locator {
    min-height: 110px;
  }

  .result-header {
    align-items: flex-start;
    flex-direction: column-reverse;
    gap: 9px;
  }

  .score-block {
    min-height: 29px;
    padding: 3px 7px;
  }

  .match-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 3px;
  }

  .read-button {
    flex: 1 1 132px;
  }

  .result-title {
    margin-top: 10px;
    font-size: var(--fs-lg);
  }

  .result-excerpt,
  .related-matches p {
    font-size: var(--fs-sm);
  }

  .result-actions {
    align-items: stretch;
  }

  .result-actions a {
    flex: 1 1 132px;
  }
}
</style>
