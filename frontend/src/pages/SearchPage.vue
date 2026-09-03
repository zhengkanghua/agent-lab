<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Bot, Search } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { authSession, useLogout } from '@/features/auth'
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
const stream = useSearchStream()
const { loggingOut, logoutError, logout } = useLogout()

/** 用户手动展开过的旧记录的 id（latest 不需要进这里，恒展开）。 */
const expandedIds = ref<Set<number>>(new Set())

const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)
const navLinks = computed(() => [
  { to: { name: 'agent-chat' }, label: 'Agent 对话', icon: Bot, visible: isSuperuser.value },
])

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

async function submitSearch(): Promise<void> {
  await stream.search()
}

/**
 * Q11：每轮搜索进入终态后清空输入、把焦点留回输入框，方便连续换词。
 *
 * 用两条 watch 协作而不是简单地在 submit 后置位：输入校验不过时（空草稿）search 不会
 * 产生新记录，任何残留置位都会在下一次真正出结果时误触发。改为「latest 记录出现新 id」
 * 置位、该轮从 loading 走向终态时消费，校验失败没有新 id，标志不会残留。
 */
let focusNextRound = false
watch(
  () => latest.value?.id,
  (id) => {
    if (id !== undefined) focusNextRound = true
  },
)
watch(
  () => latest.value?.status,
  async (status, previous) => {
    if (!focusNextRound) return
    if (previous !== 'loading' || status === 'loading' || status === undefined) return
    focusNextRound = false
    stream.draft.value = ''
    await nextTick()
    composerRef.value?.focusInput()
  },
)

async function chooseExample(value: string): Promise<void> {
  stream.draft.value = value
  await stream.search()
}

async function clearStream(): Promise<void> {
  stream.clear()
  expandedIds.value = new Set()
}

async function retryRecord(record: SearchRecord): Promise<void> {
  await stream.retry(record.query)
}

function openDocument(result: NewsReadableResult, trigger: HTMLButtonElement | null): void {
  void reader.open(result, trigger)
}
</script>

<template>
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="新闻语义研究台"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="search-workspace"
    skip-label="跳到检索工作台"
    :nav-links="navLinks"
    mode-label="按新闻检索"
    mode-detail="只给原文"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Search :size="19" stroke-width="2.2" /></template>

    <main id="search-workspace" class="workspace" :class="{ 'is-empty': !hasRecords }">
      <h1 class="sr-only">新闻语义检索</h1>

      <!-- 顶部常驻输入条。检索页不渲染页脚：底部要让位给向下长的检索流。 -->
      <div class="composer-dock" :class="{ 'is-sticky': hasRecords }">
        <SearchComposer
          ref="composerRef"
          v-model="stream.draft.value"
          :document-limit="stream.documentLimit.value"
          :matches-per-document="stream.matchesPerDocument.value"
          :loading="stream.isSearching.value"
          :input-error="stream.inputError.value"
          :remaining-characters="stream.remainingCharacters.value"
          :has-records="hasRecords"
          @update:document-limit="stream.documentLimit.value = $event"
          @update:matches-per-document="stream.matchesPerDocument.value = $event"
          @submit="submitSearch"
          @clear="clearStream"
        />
      </div>

      <!-- 空态：还没有任何检索记录。居中一句引导 + 示例，点一下直接搜。 -->
      <div v-if="!hasRecords" class="empty-state">
        <div class="empty-lead">
          <h2>从一个研究问题开始</h2>
          <p>
            输入要研究的新闻主题，结果会按新闻分组返回原始片段。每次搜索都会往下追加成一条记录，刷新后清空。
          </p>
        </div>

        <div class="example-list" aria-label="示例检索">
          <button
            v-for="example in SEARCH_EXAMPLES"
            :key="example"
            type="button"
            @click="chooseExample(example)"
          >
            <span>{{ example }}</span>
          </button>
        </div>
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
          @retry="retryRecord(record)"
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
/* 整页占满「视口 - 顶栏」；单列检索流用阅读宽度居中，比 agent 页略宽一点容纳结果卡。 */
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
  width: min(100%, calc(var(--reading-width) + 80px));
  margin: 0 auto;
  padding: 16px 0 14px;
  background: var(--surface-base);
  border-bottom: 1px solid transparent;
  transition:
    border-bottom-color var(--duration-normal) var(--ease-out-smooth),
    background-color var(--duration-normal) var(--ease-out-smooth);
  z-index: 8;
}

.composer-dock.is-sticky {
  position: sticky;
  top: var(--app-topbar-height, 69px);
  border-bottom-color: var(--surface-sunken);
  backdrop-filter: blur(12px);
  /* 半透明表面 + 模糊 = --surface-scrim 的本职（AdminShell 顶栏同款）。 */
  background: var(--surface-scrim);
}

.stream {
  display: grid;
  gap: 12px;
  width: min(100%, calc(var(--reading-width) + 80px));
  margin: 0 auto;
  padding: 4px 0 90px;
}

/* 空态沿用 agent 页的居中引导：标题 + 一句说明 + 示例词，不再是一张独立卡片。 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: min(100%, var(--reading-width));
  margin: 0 auto;
  text-align: center;
}

.empty-lead h2 {
  color: var(--text-primary);
  font-size: 1.72rem;
  font-weight: 780;
  line-height: 1.2;
  letter-spacing: -0.01em;
}

.empty-lead p {
  max-width: 52ch;
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.7;
}

.example-list {
  display: grid;
  gap: 8px;
  width: 100%;
  max-width: 620px;
  margin-top: 28px;
}

.example-list button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 46px;
  padding: 10px 14px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.88rem;
  text-align: center;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    transform var(--duration-fast) var(--ease-in-out-back);
}

.example-list button:hover {
  border-color: var(--accent);
  color: var(--accent);
  transform: translateY(-1px);
}

.example-list button:active {
  transform: translateY(0) scale(0.98);
  transition-duration: calc(var(--duration-fast) / 2);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

@media (max-width: 600px) {
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

  .empty-lead h2 {
    font-size: 1.5rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .example-list button {
    transition-property: border-color, color;
  }

  .example-list button:hover,
  .example-list button:active {
    transform: none;
  }
}
</style>
