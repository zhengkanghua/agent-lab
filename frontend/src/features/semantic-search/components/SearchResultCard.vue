<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpenText, ChevronDown, Clock3, ExternalLink, Layers3, Tag } from '@lucide/vue'
import {
  collapseExcerpt,
  formatAuthorLine,
  formatPublishedAt,
  formatScore,
  isExcerptLong,
  type NewsDocumentResult,
} from '../model/search-result'

const props = defineProps<{
  result: NewsDocumentResult
}>()

const emit = defineEmits<{
  read: [result: NewsDocumentResult, trigger: HTMLButtonElement | null]
}>()

const showAdditional = ref(false)
const bestExpanded = ref(false)
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
    <header class="result-header">
      <h3 class="result-title">{{ result.title }}</h3>
      <div
        class="score-chip"
        :aria-label="`最高 Cosine 相关度分数 ${formatScore(result.bestScore)}，不是概率`"
        title="最高 Cosine 相关度分数，不是概率"
      >
        {{ formatScore(result.bestScore) }}
      </div>
    </header>

    <div class="result-meta">
      <span v-if="result.knowledgeBaseName" class="knowledge-base-name">{{
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

    <section class="best-match" aria-label="最高分相关片段">
      <p class="match-heading">
        片段 {{ result.bestMatch.chunkIndex + 1 }} / {{ result.bestMatch.chunkCount }}
      </p>
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
          showAdditional ? '收起相关片段' : `查看另外 ${result.additionalMatches.length} 个相关片段`
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
  </article>
</template>

<style scoped>
/* 全部样式收在组件内：styles/components/result-card.css 原是「按片段」卡片与本文
   件共享的层，重构去掉按片段后只剩这一个使用方，共享层反而多了一跳。折叠（消融）
   回组件，跳转与文件数各少一份。 */
.result-card {
  padding: 24px 0;
  border-bottom: 1px solid var(--surface-sunken);
  background: transparent;
}

.result-card:last-child {
  border-bottom: none;
}

.result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

/* 标题是这张卡的第一层级：16/600，比 fs-lg 卡片标题低一档——检索流里
   十几张卡连排，每张都想当标题时就没有标题了（方案 §三.2）。 */
.result-title {
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  letter-spacing: 0;
  line-height: var(--lh-heading);
}

/* score 是参考数字不是得分徽章：透明底、弱描边、等宽字，安静地待在标题右侧。
   「不是概率」的语义承诺走 aria-label 与 title。 */
.score-chip {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  line-height: var(--lh-ui);
}

.result-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 0;
  margin-top: 7px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

/* 分隔竖线只画在兄弟之间（纯 CSS，条件渲染不用管分隔符）：
   首项（KB 名可能缺位）自动不挨打。 */
.result-meta > * + * {
  margin-left: 10px;
  border-left: 1px solid var(--border-subtle);
  padding-left: 10px;
}

.source-name {
  max-width: min(260px, 100%);
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-base-name {
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  color: var(--accent);
  background: var(--accent-soft);
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

.best-match {
  margin-top: 12px;
}

/* 「片段 x / y」自己的颜色，与卡片强调色无关（related-heading 同款）。 */
.match-heading {
  color: var(--text-tertiary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
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

/* 最佳命中包进引用块：左侧一道 accent-soft 细条点出「这是证据」，底色用
   color-mix 兑半透的凹面，与卡片其余正文区隔开半档（先例见 UserAccountRow）。 */
.result-excerpt {
  margin-top: 8px;
  padding: 10px 12px;
  border-left: 2px solid var(--accent-soft);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--surface-sunken) 50%, transparent);
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
  gap: 6px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--surface-sunken);
}

/* 阅读全文/访问原文是安静 ghost 键：这两个动作属于「接下来做什么」，
   不是这一屏的主操作，不该比内容更响。 */
.read-button,
.result-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 7px 12px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  text-decoration: none;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.read-button:hover,
.result-actions a:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

@media (pointer: coarse) {
  .read-button,
  .result-actions a {
    min-height: var(--tap-target);
  }
}

.origin-missing {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}
</style>
