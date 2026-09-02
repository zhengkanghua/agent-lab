<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, BookOpenText, Search, ShieldCheck } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { authSession } from '@/features/auth/auth-session'
import { useLogout } from '@/features/auth/useLogout'
import DocumentReader from '@/features/semantic-search/components/DocumentReader.vue'
import SearchComposer from '@/features/semantic-search/components/SearchComposer.vue'
import SearchResults from '@/features/semantic-search/components/SearchResults.vue'
import { useChunkSearch } from '@/features/semantic-search/composables/useChunkSearch'
import { useDocumentReader } from '@/features/semantic-search/composables/useDocumentReader'
import { useSemanticSearch } from '@/features/semantic-search/composables/useSemanticSearch'
import type { NewsReadableResult, SearchMode } from '@/features/semantic-search/model/search-result'

const mode = ref<SearchMode>('document')
const { loggingOut, logoutError, logout } = useLogout()
const documentSearch = useSemanticSearch()
const chunkSearch = useChunkSearch()
const reader = useDocumentReader()

// 两个模式各自维护完整的请求状态，页面只需要知道「当前是哪一个」。所有 query 状态和
// 动作都从这一个 computed 派生，新增模式时不必再逐个补分叉。
const activeSearch = computed(() => (mode.value === 'document' ? documentSearch : chunkSearch))

const activeQuery = computed({
  get: () => activeSearch.value.query.value,
  set: (value: string) => {
    activeSearch.value.query.value = value
  },
})
const activeStatus = computed(() => activeSearch.value.status.value)
const activeInputError = computed(() => activeSearch.value.inputError.value)
const activeRequestError = computed(() => activeSearch.value.requestError.value)
const activeLastQuery = computed(() => activeSearch.value.lastQuery.value)
const activeRemainingCharacters = computed(() => activeSearch.value.remainingCharacters.value)

const modeCopy = computed(() =>
  mode.value === 'document'
    ? {
        badge: '按新闻分组',
        intro: '输入一个问题或主题，工作台会按语义相关性分组新闻，并保留来源与发布时间。',
        fact: '每篇新闻集中展示',
      }
    : {
        badge: '原始片段模式',
        intro: '输入一个问题或主题，工作台会逐条展示向量检索返回的原始新闻片段。',
        fact: '每个 Chunk 独立展示',
      },
)

// 前台顶栏只放「Agent 对话」这一个功能跳转，后台（账号管理等）不再从这里直达——
// 统一走右上角账号设置 → 账号设置页的「管理员功能」入口，让前台保持纯工作台路线。
const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)
const navLinks = computed(() => [
  { to: { name: 'agent-chat' }, label: 'Agent 对话', icon: Bot, visible: isSuperuser.value },
])

function switchMode(nextMode: SearchMode): void {
  if (mode.value === nextMode) return
  // 每个模式维护独立请求状态；切换时取消在途请求，避免隐藏响应在返回后变成陈旧结果。
  documentSearch.reset()
  chunkSearch.reset()
  mode.value = nextMode
}

function submitSearch(): Promise<void> {
  return activeSearch.value.search()
}

function clearSearch(): void {
  activeSearch.value.clear()
}

function retrySearch(): Promise<void> {
  return activeSearch.value.retry()
}

async function chooseExample(value: string): Promise<void> {
  activeQuery.value = value
  await submitSearch()
}

function openDocument(result: NewsReadableResult, trigger: HTMLButtonElement | null): void {
  void reader.open(result, trigger)
}
</script>

<template>
  <!-- mode-detail 写「本页只给原文」是认真的：本页不生成答案，只返回检索到的原文
       片段。要模型作答请走 /agent。 -->
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="新闻语义研究台"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="search-workspace"
    skip-label="跳到检索工作台"
    :nav-links="navLinks"
    :mode-label="modeCopy.badge"
    mode-detail="本页只给原文"
    footer-brand="Signal Desk"
    footer-note="新闻分组 / 原始片段 · 原文可追溯 · 只读访问"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Search :size="19" stroke-width="2.2" /></template>

    <main id="search-workspace" class="content-wrap workspace">
      <aside class="search-pane" aria-labelledby="page-title">
        <div class="workspace-intro">
          <p class="workspace-label">新闻研究工作台</p>
          <h1 id="page-title">
            <span>从新闻原文里，</span>
            <span>找到相关证据。</span>
          </h1>
          <p>{{ modeCopy.intro }}</p>

          <div class="workspace-facts" aria-label="检索结果说明">
            <span>
              <BookOpenText :size="16" aria-hidden="true" />
              {{ modeCopy.fact }}
            </span>
            <span><ShieldCheck :size="16" aria-hidden="true" />全文按需读取</span>
          </div>
        </div>

        <SearchComposer
          v-model="activeQuery"
          :mode="mode"
          :document-limit="documentSearch.documentLimit.value"
          :top-k="chunkSearch.topK.value"
          :matches-per-document="documentSearch.matchesPerDocument.value"
          :loading="activeStatus === 'loading'"
          :input-error="activeInputError"
          :remaining-characters="activeRemainingCharacters"
          @update:mode="switchMode"
          @update:document-limit="documentSearch.documentLimit.value = $event"
          @update:top-k="chunkSearch.topK.value = $event"
          @update:matches-per-document="documentSearch.matchesPerDocument.value = $event"
          @submit="submitSearch"
          @clear="clearSearch"
        />
      </aside>

      <SearchResults
        class="results-pane"
        :mode="mode"
        :status="activeStatus"
        :results="documentSearch.results.value"
        :chunk-results="chunkSearch.results.value"
        :error="activeRequestError"
        :last-query="activeLastQuery"
        @retry="retrySearch"
        @choose-example="chooseExample"
        @read="openDocument"
      />
    </main>

    <DocumentReader
      :open="reader.isOpen.value"
      :result="reader.selectedResult.value"
      :detail="reader.detail.value"
      :loading="reader.isLoading.value"
      :error="reader.error.value"
      :hash-mismatch="reader.contentHashMismatch.value"
      @close="reader.close"
      @closed="reader.restoreFocus"
      @retry="reader.retry"
    />
  </AppShell>
</template>

<style scoped>
.workspace {
  display: grid;
  grid-template-columns: minmax(310px, 380px) minmax(0, 1fr);
  align-items: start;
  gap: 34px;
  min-height: calc(100vh - 126px);
  padding-top: 34px;
  padding-bottom: 64px;
}

.search-pane {
  position: sticky;
  top: 102px;
  min-width: 0;
}

.workspace-intro {
  padding: 3px 4px 27px;
}

.workspace-label {
  color: var(--accent);
  font-size: 0.76rem;
  font-weight: 760;
}

.workspace-intro h1 {
  max-width: 380px;
  margin-top: 10px;
  color: var(--text-primary);
  font-size: 2.25rem;
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.16;
}

.workspace-intro h1 span {
  display: block;
}

.workspace-intro > p:not(.workspace-label) {
  max-width: 36ch;
  margin-top: 16px;
  color: var(--text-secondary);
  font-size: 0.94rem;
  line-height: 1.72;
}

.workspace-facts {
  display: grid;
  gap: 9px;
  margin-top: 23px;
  padding-top: 16px;
  border-top: 1px solid var(--border-subtle);
}

.workspace-facts span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 0.78rem;
}

.workspace-facts svg {
  color: var(--accent);
}

.results-pane {
  min-width: 0;
}

/* 顶栏与页脚的样式随结构一起搬到 layouts/AppShell.vue。 */

@media (max-width: 980px) {
  .workspace {
    grid-template-columns: 1fr;
    gap: 35px;
    padding-top: 30px;
  }

  .search-pane {
    position: static;
  }

  .workspace-intro h1,
  .workspace-intro > p:not(.workspace-label) {
    max-width: 620px;
  }

  .workspace-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-width: 620px;
  }
}

@media (max-width: 560px) {
  .workspace {
    gap: 29px;
    padding-top: 24px;
    padding-bottom: 46px;
  }

  .workspace-intro {
    padding-bottom: 22px;
  }

  .workspace-intro h1 {
    max-width: none;
    font-size: 1.95rem;
  }

  .workspace-intro > p:not(.workspace-label) {
    font-size: 0.9rem;
  }

  .workspace-facts {
    grid-template-columns: 1fr;
  }
}
</style>
