<script setup lang="ts">
import { computed, ref } from 'vue'
import { Bot, Search } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { authSession } from '@/features/auth/auth-session'
import { useLogout } from '@/features/auth/useLogout'
import DocumentReader from '@/features/semantic-search/components/DocumentReader.vue'
import SearchComposer from '@/features/semantic-search/components/SearchComposer.vue'
import SearchResults from '@/features/semantic-search/components/SearchResults.vue'
import { SEARCH_EXAMPLES } from '@/features/semantic-search/constants/examples'
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

/** 是否已经提交过一次检索（不再处于「等待输入」的 idle）。初始态用它在屏幕中央展示
 *  大输入框与引导；一旦提交（无论 loading/empty/有结果），就转成紧凑吸顶输入条，
 *  把屏幕让给检索内容。empty 也算已检索：那种情况要给空结果态让位。 */
const hasSearched = computed(() => activeStatus.value !== 'idle')

// 顶栏 mode-label 用的短徽标文案。模式本身的详细引导放在 SearchComposer 的
// mode-description 里（紧挨切换控件），这里不再带一套重复的 intro/fact。
const modeBadge = computed(() => (mode.value === 'document' ? '按新闻分组' : '原始片段模式'))

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
    :mode-label="modeBadge"
    mode-detail="本页只给原文"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Search :size="19" stroke-width="2.2" /></template>

    <!-- 检索页按 Agent 对话那种「输入 + 内容」两段式重组（底部输入换成顶部输入）：
         - idle（还没检索）：搜索框在视口中央的大引导形态，下方放示例，方便第一次上手；
         - 已检索：搜索框收细、吸在顶部可随时换词，把大部分屏幕让给检索内容。
         切换全在 .workspace 的 is-idle / is-searched 两个类上做，不搬组件。
         这一页不渲染页脚：底部要让位给结果内容区，页脚那句「只读访问」由顶栏
         mode-note 承担，与 Agent 页同理。 -->
    <main
      id="search-workspace"
      class="content-wrap workspace"
      :class="hasSearched ? 'is-searched' : 'is-idle'"
    >
      <!-- 页级标题给读屏与文档大纲；视觉标题由 SearchComposer 自带的可见标题承担。 -->
      <h1 class="sr-only">新闻语义检索</h1>

      <!-- 一条连续的中置「检索流」：输入框与结果是同一条纵向流里的相邻成员，共用同一条
           列宽与左对齐基线，视觉上浑然一体（不再让输入卡居中窄、结果却贴左全宽，那会
           把一页割成两块内容）。idle 时输入框在这条流里靠垂直居中大引导；检索后输入框
           以紧凑条固定在这条流顶部，结果直接续排其下。 -->
      <div class="flow">
        <div class="flow-input">
          <SearchComposer
            v-model="activeQuery"
            :slim="hasSearched"
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

          <!-- idle 时的引导：点一下就能跑的示例，跟在输入框下面同一列。 -->
          <div v-if="!hasSearched" class="flow-examples">
            <p class="hint-lead">先试试这些例子</p>
            <div class="hint-examples" aria-label="示例检索">
              <button
                v-for="example in SEARCH_EXAMPLES"
                :key="example"
                type="button"
                @click="chooseExample(example)"
              >
                {{ example }}
              </button>
            </div>
          </div>
        </div>

        <!-- 检索内容（loading/空态/结果）作为流的下一段直接续在输入下方，同一条列。 -->
        <div v-if="hasSearched" class="flow-results">
          <SearchResults
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
        </div>
      </div>
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
/* 整页占满「视口 - 顶栏」，输入与结果共用同一条垂直流，不产生额外空隙。
   --app-topbar-height 由 AppShell 提供，两个 compact 断点会改写它。 */
.workspace {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - var(--app-topbar-height, 69px));
  min-height: calc(100dvh - var(--app-topbar-height, 69px));
}

/* 唯一的一条中置列：输入卡与结果卡都铺满它、左对齐基线一致，视觉上是一条连续流。
   宽度取「比 content-wrap 全宽收敛、又不像纯正文那么窄」的中值，结果卡不会过长。 */
.flow {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 1000px;
  margin-inline: auto;
}

/* ---- idle：输入作为这条流的第一段，垂直居中大引导，示例居中跟在下面 ---- */
.is-idle .flow {
  flex: 1 1 auto;
  justify-content: center;
}

.is-idle .flow-input {
  width: 100%;
  text-align: center;
}

/* idle 的输入卡本身仍左对齐排版（让输入区可读），外层文字才居中。 */
.is-idle .flow-input :deep(.composer) {
  text-align: left;
}

.flow-examples {
  margin-top: 18px;
}

.hint-lead {
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 720;
}

.hint-examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 9px;
}

.hint-examples button {
  padding: 6px 13px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.78rem;
  transition:
    border-color 150ms ease,
    color 150ms ease;
}

.hint-examples button:hover {
  border-color: var(--accent);
  color: var(--accent);
}

/* ---- 已检索：输入作为流的固定头部，结果作为流的延续 ---- */
.is-searched .flow {
  flex: 1 1 auto;
}

.is-searched .flow-input {
  position: sticky;
  z-index: 8;
  top: var(--app-topbar-height, 69px);
  width: 100%;
  padding: 12px 0 10px;
  background: var(--surface-scrim);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-subtle);
}

/* 结果是这条流的下一段，与输入同宽，直接续排其下，不再作为另一块版面。 */
.flow-results {
  padding: 20px 0 60px;
}

/* 顶栏与页脚的样式随结构一起搬到 layouts/AppShell.vue。 */

@media (max-width: 980px) {
  .is-searched .flow-input {
    padding: 10px 0 9px;
  }
}

@media (max-width: 560px) {
  .is-idle .flow {
    justify-content: flex-start;
    padding-top: 26px;
  }

  .flow-results {
    padding-top: 14px;
  }
}
</style>
