<script setup lang="ts">
import { computed, ref } from 'vue'
import { BookOpenText, LogOut, Search, ShieldCheck, UserRound, UsersRound } from '@lucide/vue'
import { useRouter } from 'vue-router'
import { queryClient } from '../app/query-client'
import { authSession } from '../features/auth/auth-session'
import DocumentReader from '../features/semantic-search/components/DocumentReader.vue'
import SearchComposer from '../features/semantic-search/components/SearchComposer.vue'
import SearchResults from '../features/semantic-search/components/SearchResults.vue'
import { useChunkSearch } from '../features/semantic-search/composables/useChunkSearch'
import { useDocumentReader } from '../features/semantic-search/composables/useDocumentReader'
import { useSemanticSearch } from '../features/semantic-search/composables/useSemanticSearch'
import type {
  NewsReadableResult,
  SearchMode,
} from '../features/semantic-search/model/search-result'

const mode = ref<SearchMode>('document')
const router = useRouter()
const loggingOut = ref(false)
const logoutError = ref(false)
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

async function logout(): Promise<void> {
  if (loggingOut.value) return

  loggingOut.value = true
  logoutError.value = false
  try {
    await authSession.logout()
    queryClient.clear()
    await router.replace({ name: 'login' })
  } catch {
    logoutError.value = true
  } finally {
    loggingOut.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#search-workspace">跳到检索工作台</a>

    <header class="topbar">
      <div class="content-wrap topbar-inner">
        <a class="brand-lockup" href="/" aria-label="Signal Desk 首页">
          <span class="brand-mark" aria-hidden="true">
            <Search :size="19" stroke-width="2.2" />
          </span>
          <span class="brand-copy">
            <strong>Signal Desk</strong>
            <small>新闻语义研究台</small>
          </span>
        </a>

        <div class="topbar-actions">
          <div class="mode-note">
            <span class="mode-dot" aria-hidden="true"></span>
            <span>{{ modeCopy.badge }}</span>
            <span class="mode-detail">不生成答案</span>
          </div>

          <div class="account-control">
            <RouterLink
              v-if="authSession.user.value?.is_superuser"
              class="admin-link"
              :to="{ name: 'user-admin' }"
              aria-label="管理平台账号"
              title="账号管理"
            >
              <UsersRound :size="17" aria-hidden="true" />
            </RouterLink>
            <span v-if="authSession.user.value" class="account-identity">
              <UserRound :size="16" aria-hidden="true" />
              <span>{{ authSession.user.value.email }}</span>
            </span>
            <button
              type="button"
              class="logout-button"
              :disabled="loggingOut"
              aria-label="退出登录"
              title="退出登录"
              @click="logout"
            >
              <LogOut :size="17" aria-hidden="true" />
            </button>
            <span v-if="logoutError" class="logout-error" role="alert">退出失败</span>
          </div>
        </div>
      </div>
    </header>

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

    <footer class="site-footer">
      <div class="content-wrap footer-inner">
        <span>Signal Desk</span>
        <span>新闻分组 / 原始片段 · 原文可追溯 · 只读访问</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.topbar {
  position: sticky;
  z-index: 10;
  top: 0;
  border-bottom: 1px solid var(--paper-300);
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(12px);
}

/* .topbar-inner 见 styles/components/topbar.css。 */

.brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

/* .brand-mark、.brand-copy 见 styles/components/topbar.css。 */

.brand-copy strong {
  font-family: var(--display-font);
  font-size: 1rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.2;
}

.brand-copy small {
  color: var(--ink-700);
  font-size: 0.72rem;
  letter-spacing: 0;
}

.mode-note {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-700);
  font-size: 0.78rem;
  white-space: nowrap;
}

.topbar-actions,
.account-control,
.account-identity {
  display: inline-flex;
  align-items: center;
}

.topbar-actions {
  gap: 18px;
}

.account-control {
  position: relative;
  gap: 7px;
  padding-left: 17px;
  border-left: 1px solid var(--paper-300);
}

.account-identity {
  max-width: 220px;
  gap: 7px;
  color: var(--ink-700);
  font-size: 0.75rem;
}

.account-identity span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-identity svg {
  flex: 0 0 auto;
  color: var(--source-600);
}

.logout-button,
.admin-link {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--ink-700);
  background: transparent;
}

.admin-link {
  text-decoration: none;
}

.logout-button:hover:not(:disabled),
.admin-link:hover {
  border-color: var(--paper-300);
  color: var(--signal-600);
  background: var(--paper-100);
}

.logout-button:disabled {
  cursor: wait;
  opacity: 0.45;
}

.logout-error {
  position: absolute;
  top: calc(100% + 9px);
  right: 0;
  color: var(--danger-600);
  font-size: 0.7rem;
  white-space: nowrap;
}

.mode-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--source-500);
  box-shadow: 0 0 0 4px var(--source-100);
}

.mode-detail {
  padding-left: 8px;
  border-left: 1px solid var(--paper-300);
  color: var(--ink-500);
}

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
  color: var(--signal-600);
  font-size: 0.76rem;
  font-weight: 760;
}

.workspace-intro h1 {
  max-width: 380px;
  margin-top: 10px;
  color: var(--ink-950);
  font-family: var(--display-font);
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
  color: var(--ink-700);
  font-size: 0.94rem;
  line-height: 1.72;
}

.workspace-facts {
  display: grid;
  gap: 9px;
  margin-top: 23px;
  padding-top: 16px;
  border-top: 1px solid var(--paper-300);
}

.workspace-facts span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-700);
  font-size: 0.78rem;
}

.workspace-facts svg {
  color: var(--source-600);
}

.results-pane {
  min-width: 0;
}

.site-footer {
  border-top: 1px solid var(--paper-300);
  background: var(--paper-50);
}

.footer-inner {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 19px 0 22px;
  color: var(--ink-500);
  font-size: 0.72rem;
}

.footer-inner span:first-child {
  color: var(--ink-800);
  font-weight: 720;
}

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
  .topbar-inner {
    min-height: 62px;
  }

  .brand-copy small,
  .mode-detail {
    display: none;
  }

  .mode-note {
    font-size: 0.72rem;
  }

  .topbar-actions {
    gap: 8px;
  }

  .account-control {
    padding-left: 8px;
  }

  .account-identity {
    display: none;
  }

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

  .footer-inner {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
}
</style>
