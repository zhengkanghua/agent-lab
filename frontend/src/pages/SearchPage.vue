<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { Bot, Search } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { authSession, useLogout } from '@/features/auth'
import { usePreferences } from '@/features/settings'
import BaseSuggestionList from '@/shared/ui/BaseSuggestionList.vue'
import KnowledgeBaseScopePicker from '@/shared/ui/KnowledgeBaseScopePicker.vue'
import { useKnowledgeBaseScope } from '@/shared/composables/useKnowledgeBaseScope'
import {
  DocumentReader,
  SearchComposer,
  SearchRecordTurn,
  SEARCH_EXAMPLES,
  useDocumentReader,
  useSearchStream,
  type NewsReadableResult,
  type SearchRecord,
} from '@/features/semantic-search'

/* 语义检索页（Q1–Q15 的落地）。
 *
 * 检索从「单次覆盖式搜索」重构为一条仿 Agent 会话体感、向下累积的检索流：
 *  - 顶部一条常驻输入框（Q3 / Q4 模型二），不再有两态跳变、也没有「按片段」模式；
 *  - 最新一次检索记录顶在输入框正下方展开，旧记录折叠成标题行往下沉（Q5 乙 / Q8 / Q9）；
 *  - 每搜一次追加一条，刷新即清空，不做真会话、不落后端（Q1 b）；
 *  - 有「清空检索流」入口（Q6）。
 *
 * 折叠态由本页维护：latest 恒展开，旧记录默认折叠、手动展开的保留在 expandedIds 里。
 */

const composerRef = ref<InstanceType<typeof SearchComposer> | null>(null)
const reader = useDocumentReader()
const scope = useKnowledgeBaseScope()

// 数量参数是设置中心的持久偏好：提交那一刻读到什么值，这一轮就用什么值。
const { preferences } = usePreferences()
const stream = useSearchStream({
  getDocumentLimit: () => preferences.documentLimit,
  getMatchesPerDocument: () => preferences.matchesPerDocument,
  getScope: () => scope.selection.value,
  getScopeError: () => scope.error.value,
})

const { loggingOut, logoutError, logout } = useLogout()

/** 用户手动展开过的旧记录的 id（latest 不需要进这里，恒展开）。 */
const expandedIds = ref<Set<number>>(new Set())

const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)
const navLinks = computed(() => [
  { to: { name: 'agent-chat' }, label: 'Agent 对话', icon: Bot, visible: isSuperuser.value },
])

/** 悬停在输入条设置入口上时给的当前值摘要。 */
const preferenceSummary = computed(
  () =>
    `每次检索 ${preferences.documentLimit} 篇 · 每篇 ${preferences.matchesPerDocument} 条（在设置中调整）`,
)

const hasRecords = computed(() => stream.records.value.length > 0)
const latest = computed<SearchRecord | null>(() => stream.latestRecord.value)

/** 渲染顺序：最新贴顶（模型二），旧的按提交先后往下沉。 */
const newestFirst = computed<SearchRecord[]>(() => [...stream.records.value].reverse())

function recordExpanded(id: number): boolean {
  return latest.value?.id === id || expandedIds.value.has(id)
}

function toggleRecord(record: SearchRecord): void {
  if (record.id === latest.value?.id) return
  const next = new Set(expandedIds.value)
  if (next.has(record.id)) next.delete(record.id)
  else next.add(record.id)
  expandedIds.value = next
}

async function submitSearch(query?: string): Promise<void> {
  const trigger = document.activeElement
  const completedId = await (query === undefined ? stream.search() : stream.retry(query))
  await nextTick()
  // 已取消的请求、打开的阅读器以及用户后来选中的控件都不接收自动聚焦。
  if (completedId === null || completedId !== latest.value?.id || reader.isOpen.value) return
  if (document.activeElement === trigger || document.activeElement === document.body) {
    composerRef.value?.focusInput()
  }
}

async function clearStream(): Promise<void> {
  stream.clear()
  expandedIds.value = new Set()
}

function openDocument(result: NewsReadableResult, trigger: HTMLButtonElement | null): void {
  void reader.open(result, trigger)
}
</script>

<template>
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="知识库工作台"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="search-workspace"
    skip-label="跳到检索工作台"
    :nav-links="navLinks"
    mode-label="文档检索"
    mode-detail="只给原文"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Search :size="19" stroke-width="2.2" /></template>

    <main id="search-workspace" class="workspace" :class="{ 'is-empty': !hasRecords }">
      <h1 class="sr-only">知识库语义检索</h1>

      <!-- 顶部常驻输入条。检索页不渲染页脚：底部要让位给向下长的检索流。 -->
      <div class="composer-dock" :class="{ 'is-sticky': hasRecords }">
        <SearchComposer
          ref="composerRef"
          v-model="stream.draft.value"
          :loading="stream.isSearching.value"
          :input-error="stream.inputError.value"
          :remaining-characters="stream.remainingCharacters.value"
          :has-records="hasRecords"
          :preference-summary="preferenceSummary"
          :disabled="scope.error.value !== null"
          @submit="submitSearch()"
          @clear="clearStream"
        />
        <KnowledgeBaseScopePicker
          v-model="scope.selection.value"
          :knowledge-bases="scope.knowledgeBases.value"
          :error="scope.error.value"
          :loading="scope.loading.value"
          @refresh="scope.refresh"
        />
      </div>

      <!-- 空态：还没有任何检索记录。只有示例，点一下直接搜。 -->
      <div v-if="!hasRecords" class="empty-state">
        <BaseSuggestionList
          :examples="SEARCH_EXAMPLES"
          aria-label="示例检索"
          @select="submitSearch"
        />
      </div>

      <!-- 检索流：最新贴顶展开，旧记录折叠。 -->
      <div v-else class="stream" aria-label="检索记录">
        <SearchRecordTurn
          v-for="record in newestFirst"
          :key="record.id"
          :record="record"
          :is-latest="record.id === latest?.id"
          :expanded="recordExpanded(record.id)"
          @toggle="toggleRecord(record)"
          @retry="submitSearch(record.query)"
          @read="openDocument"
        />
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
/* 整页占满「视口 - 顶栏」；单列检索流用检索流宽度居中，比 agent 页的阅读宽度
   宽一档容纳结果卡的混合排版（令牌取舍见 tokens.css 与 ADR 0016）。 */
.workspace {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - var(--app-topbar-height, 69px));
  min-height: calc(100dvh - var(--app-topbar-height, 69px));
}

.workspace.is-empty {
  justify-content: center;
}

.composer-dock {
  width: min(100%, calc(var(--stream-width) + 80px));
  margin: 0 auto;
  padding: 16px 0 14px;
  /* 底色与页面同色，常态不可见；吸附后换成 --surface-scrim。这里刻意不加
     transition：background-color 的 250ms 渐变会让主题切换的瞬间留下一块
     「旧底色矩形」——整页瞬时翻转，唯独它还在渐变（2026-09 老板录屏实测）。 */
  background: var(--surface-base);
  border-bottom: 1px solid transparent;
  z-index: var(--z-dock);
}

.composer-dock.is-sticky {
  position: sticky;
  top: var(--app-topbar-height, 69px);
  border-bottom-color: var(--surface-sunken);
  /* 半透明表面用 --surface-scrim（96% 不透明）。不配 backdrop-filter：
     那点模糊肉眼不可见，却会在主题切换时闪出一帧黑色矩形（Chromium 伪影）。 */
  background: var(--surface-scrim);
}

.stream {
  display: grid;
  gap: 12px;
  width: min(100%, calc(var(--stream-width) + 80px));
  margin: 0 auto;
  padding: 4px 0 90px;
}

/* 空态沿用 agent 页的居中引导：示例建议卡收在阅读宽度内。 */
.empty-state {
  width: min(100%, calc(var(--reading-width) - 140px));
  margin: 0 auto;
}

@media (max-width: 560px) {
  .composer-dock {
    width: calc(100% - 24px);
    padding: 12px 0 10px;
  }

  .stream {
    width: calc(100% - 24px);
    padding-top: 2px;
  }

  .empty-state {
    width: calc(100% - 24px);
  }
}
</style>
