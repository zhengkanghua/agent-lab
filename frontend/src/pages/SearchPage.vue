<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import AppShell from '@/layouts/AppShell.vue'
import { useLogout } from '@/features/auth'
import { usePreferences } from '@/features/settings'
import BaseSuggestionList from '@/shared/ui/BaseSuggestionList.vue'
import KnowledgeBaseScopePicker from '@/shared/ui/KnowledgeBaseScopePicker.vue'
import ScrollToBottomButton from '@/shared/ui/ScrollToBottomButton.vue'
import { useKnowledgeBaseScope } from '@/shared/composables/useKnowledgeBaseScope'
import { useStickToBottom } from '@/shared/composables/useStickToBottom'
import {
  SearchComposer,
  SearchRecordTurn,
  SEARCH_EXAMPLES,
  useSearchStream,
  type SearchRecord,
} from '@/features/semantic-search'
import { useDocumentReader } from '@/shared/composables/useDocumentReader'
import type { ReadableResult } from '@/shared/model/readable-result'
import DocumentReader from '@/shared/ui/DocumentReader.vue'

/* 语义检索页（Q1–Q15 的落地 + 2026-10 输入坞下移）。
 *
 * 检索流是「仿 Agent 会话体感、向下累积」的：一条条检索记录按提交先后从上往下排，
 * 最新的那条在最下面、贴着输入坞展开，旧记录折叠成标题行留在上方（Q5 乙 / Q8 / Q9）。
 * 刷新即清空，不做真会话、不落后端（Q1 b）。有「清空检索流」入口（Q6）。
 *
 * 输入坞从顶部挪到底部：与 Agent 对话页同一形态。原来输入坞常驻顶部、最新记录贴顶，
 * 是为了让新结果紧挨着输入框出现；但同一件事用「输入在下面、内容往上长」表达更符合
 * 聊天类界面的习惯，也让两页共用同一个外壳、同一个浮层与同一套滚动规则。
 *
 * 折叠态由本页维护：最新的那条恒展开，旧记录默认折叠、手动展开的保留在 expandedIds 里。
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

/* 记录区的贴底跟随：结果落地时记录会长高，贴着底看的人不该被落下；
   已经上翻的人由「回到最新」给出回去的入口。规则与 Agent 页完全一致。 */
const streamRegionRef = ref<HTMLElement | null>(null)
const {
  atBottom: streamAtBottom,
  hasNewContent: streamHasNewContent,
  scrollToBottom,
  syncAtBottom,
} = useStickToBottom(streamRegionRef)

/** 用户手动展开过的旧记录的 id（最新的那条不需要进这里，恒展开）。 */
const expandedIds = ref<Set<number>>(new Set())

/** 输入条上那枚偏好入口显示的当前值（悬停提示与无障碍名也用它）。 */
const preferenceSummary = computed(
  () => `每次 ${preferences.documentLimit} 篇 · 每篇 ${preferences.matchesPerDocument} 条`,
)

const hasRecords = computed(() => stream.records.value.length > 0)
const latest = computed<SearchRecord | null>(() => stream.latestRecord.value)

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
  // 提交是「我要看这一次的结果」：把视口带到最新一条（旧的在上、新的贴着输入坞）。
  scrollToBottom('smooth')
}

async function clearStream(): Promise<void> {
  stream.clear()
  expandedIds.value = new Set()
}

/** 外壳主操作「新检索」：清空检索流、把焦点还给输入框，和 ChatGPT 的 New chat 同一体感。 */
async function startNewSearch(): Promise<void> {
  await clearStream()
  await nextTick()
  // 清空后文档一下变短，浏览器会夹住滚动位置；这里对一次状态，免得浮层留在屏幕上。
  syncAtBottom()
  composerRef.value?.focusInput()
}

function openDocument(result: ReadableResult, trigger: HTMLButtonElement | null): void {
  void reader.open(result, trigger)
}
</script>

<template>
  <AppShell
    active="search"
    main-id="search-workspace"
    skip-label="跳到检索工作台"
    primary-label="新检索"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @primary="startNewSearch"
    @logout="logout"
  >
    <main id="search-workspace" class="workspace" :class="{ 'is-empty': !hasRecords }">
      <h1 class="sr-only">知识库语义检索</h1>

      <div ref="streamRegionRef" class="stream-region" :class="{ 'is-empty': !hasRecords }">
        <!-- 空态：还没有任何检索记录。问候是主角，示例点一下直接搜。 -->
        <template v-if="!hasRecords">
          <h2 class="empty-greeting">想查点什么？</h2>
          <div class="empty-state">
            <BaseSuggestionList
              :examples="SEARCH_EXAMPLES"
              aria-label="示例检索"
              @select="submitSearch"
            />
            <!-- 「只给原文」的工具定位说明：空态正是「这个页面是什么」的说明位。 -->
            <p class="empty-note">文档检索 · 只给原文</p>
          </div>
        </template>

        <!-- 检索流：按提交先后从上往下排，最新的那条在最下面、贴着输入坞。 -->
        <div v-else class="stream" aria-label="检索记录">
          <SearchRecordTurn
            v-for="record in stream.records.value"
            :key="record.id"
            :record="record"
            :is-latest="record.id === latest?.id"
            :expanded="recordExpanded(record.id)"
            @toggle="toggleRecord(record)"
            @retry="submitSearch(record.query)"
            @read="openDocument"
          />
        </div>
      </div>

      <div class="composer-dock" :class="{ 'has-history': hasRecords }">
        <ScrollToBottomButton
          :open="hasRecords && !streamAtBottom"
          :has-new-content="streamHasNewContent"
          @jump="scrollToBottom('smooth')"
        />
        <SearchComposer
          ref="composerRef"
          v-model="stream.draft.value"
          :loading="stream.isSearching.value"
          :input-error="stream.inputError.value"
          :remaining-characters="stream.remainingCharacters.value"
          :preference-summary="preferenceSummary"
          :disabled="scope.error.value !== null"
          @submit="submitSearch()"
        >
          <template #scope>
            <KnowledgeBaseScopePicker
              v-model="scope.selection.value"
              :knowledge-bases="scope.knowledgeBases.value"
              :error="scope.error.value"
              :loading="scope.loading.value"
              @refresh="scope.refresh"
            />
          </template>
        </SearchComposer>
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
/* 整页占满「视口 - 汉堡条」（桌面端没有 bar，值即视口高）。 */
.workspace {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - var(--app-header-offset, 0px));
  min-height: calc(100dvh - var(--app-header-offset, 0px));
}

/* 记录区吃掉剩余高度：空态在这一格里居中，有记录时从顶部正常流动。
   flex: 1 也不只是为了让空态居中——不占满剩余高度，输入坞就浮在半空。 */
.stream-region {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  padding-top: var(--space-6);
}

.stream-region.is-empty {
  justify-content: center;
  padding-bottom: var(--space-2);
}

/* 只有「矮视口」需要把空态压到顶部——内容比视口还高时，居中会把问候推出屏幕。
   宽度不是理由：390×844 的手机又窄又高，居中完全放得下，ChatGPT / Gemini 的空态
   也是居中的。原先这条带着 `max-width: 560px`，于是窄而高的手机被当成矮视口，
   上方白白空出半屏（2026-10 与老板核对后收窄条件）。 */
@media (max-height: 700px) {
  .stream-region.is-empty {
    justify-content: flex-start;
  }
}

/* 检索流比 Agent 的阅读列宽一档：结果卡是「标签 + 标题 + 片段」的混合排版，
   纯正文宽度会让标签换行（令牌取舍见 tokens.css）。 */
.stream {
  display: grid;
  gap: var(--space-3);
  width: min(100%, calc(var(--stream-width) + 80px));
  margin: 0 auto;
  padding: 0 0 var(--space-2);
}

/* 开始检索后输入坞贴底，空态保持正常流向，避免遮住尚未点击的建议。
   sticky 留在文档流里，记录区不需要额外预留输入坞的高度。
   顶部那道渐变是让滚上来的记录在贴近输入坞时淡出，而不是被一条硬边裁断。 */
.composer-dock {
  width: min(100%, calc(var(--stream-width) + 80px));
  margin: 0 auto;
  padding: var(--space-3) 0 var(--space-2-5);
  background: linear-gradient(to bottom, transparent, var(--surface-base) 22%);
  z-index: var(--z-dock);
}

.composer-dock.has-history {
  position: sticky;
  bottom: 0;
}

/* 空态问候是这一屏的主角（2026-09 重设计 P2-D）。展示字体令牌只给
   空态问候与登录主标这两处开关，默认回退无衬线。 */
.empty-greeting {
  margin: 0 0 var(--space-4);
  color: var(--text-primary);
  font-family: var(--display-font);
  font-size: var(--fs-3xl);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-heading);
  text-align: center;
}

/* 空态沿用 agent 页的居中引导：示例建议卡收在阅读宽度内。 */
.empty-state {
  width: min(100%, calc(var(--reading-width) - 140px));
  margin: 0 auto;
}

.empty-note {
  margin: var(--space-3-5) 0 0;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  text-align: center;
}

@media (max-width: 560px) {
  .stream-region {
    padding-top: var(--space-4);
  }

  .composer-dock {
    width: calc(100% - 24px);
    padding: var(--space-2-5) 0 var(--space-2);
  }

  .stream {
    width: calc(100% - 24px);
    padding-top: var(--space-0-5);
  }

  .empty-state {
    width: calc(100% - 24px);
  }
}
</style>
