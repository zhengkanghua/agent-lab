<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { History, ShieldCheck } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { useLogout } from '@/features/auth'
import { usePreferences } from '@/features/settings'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import KnowledgeBaseScopePicker from '@/shared/ui/KnowledgeBaseScopePicker.vue'
import { useKnowledgeBaseScope } from '@/shared/composables/useKnowledgeBaseScope'
import {
  AgentComposer,
  AgentTranscript,
  ThreadSidebar,
  useAgentChat,
  useThreadList,
  AGENT_EXAMPLES,
} from '@/features/agent-chat'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'
import type { DocumentEvidence } from '@/api/agent-evidence'
import { DocumentReader, useDocumentReader } from '@/features/semantic-search'

const route = useRoute()
const router = useRouter()

// 自定义系统提示词是设置中心的持久偏好：发送时从偏好 store 读，本页不再持有编辑状态。
const { preferences } = usePreferences()

const chat = useAgentChat({
  getSystemPrompt: () => preferences.agentSystemPrompt,
  getScopeError: () => scope.error.value,
  onThreadCreated: (id) => {
    void router.replace({ name: 'agent-thread', params: { threadId: id } })
    threadList.acceptCreatedThread(createdSummary(id))
  },
})
const scope = useKnowledgeBaseScope(chat.selection)
const reader = useDocumentReader()
const selectedEvidence = ref<DocumentEvidence | null>(null)

function openEvidence(evidence: DocumentEvidence, trigger: HTMLElement): void {
  selectedEvidence.value = evidence
  void reader.open(
    {
      documentId: evidence.document_id,
      knowledgeBaseId: evidence.knowledge_base_id,
      knowledgeBaseName: evidence.knowledge_base_name,
      contentHash: evidence.content_hash,
      title: evidence.title,
      sourceName: evidence.source_name ?? null,
      uploadFilename: evidence.upload_filename ?? null,
      url: evidence.url ?? null,
      publishedAt: evidence.published_at ?? null,
      labels: [],
      authors: [],
    },
    trigger,
  )
}

const threadList = useThreadList({
  activeThreadId: () => chat.threadId.value,
  // 删掉的正是当前打开的那个：清空界面并回到 /agent。留在 /agent/:id 上会让刷新页面时
  // 又去读一个已经不存在的会话，得到一条本可避免的错误。
  onActiveThreadDeleted: () => {
    chat.startNewConversation()
    void router.replace({ name: 'agent-chat' })
  },
})

// beforeLogout 里掐掉在途的流：留着它会在退出后继续读一条已经没有权限的连接。
const { loggingOut, logoutError, logout } = useLogout({ beforeLogout: chat.cancel })

const transcriptEndRef = ref<HTMLElement | null>(null)

const hasHistory = computed(() => chat.turns.value.length > 0)

/** 路由参数里的会话 id。`/agent` 上没有这个参数，值为 null。 */
const routeThreadId = computed(() => {
  const value = route.params.threadId
  const id = Array.isArray(value) ? value[0] : value
  return id ? String(id) : null
})

onMounted(() => {
  void threadList.load()
})

/*
 * 路由参数是唯一的真相来源：URL 变了就按它切会话，包括前进后退。
 *
 * 用 immediate 覆盖首次进入，所以直接访问 /agent/<id> 也会载入历史，不必在 onMounted 里
 * 再写一遍同样的逻辑。
 *
 * 守卫 `id === chat.threadId` 是必须的：`send()` 新建会话拿到 id 后会 replace 路由，
 * 那次 replace 会触发本 watch；不守卫的话它会立刻去回放这个刚建出来的会话，把刚刚流式
 * 生成的那一轮覆盖成从服务端读回来的版本——看起来像界面闪一下重画，实际是多余的一次请求。
 */
watch(
  routeThreadId,
  (id) => {
    void reader.close()
    selectedEvidence.value = null
    if (id === null) {
      // 从某个会话回到 /agent（点「新对话」或后退）时清空，否则旧会话的历史留在界面上，
      // 而 threadId 已经没了，下一轮会开一个新会话。
      chat.startNewConversation()
      return
    }
    if (id === chat.threadId.value) return
    void chat.loadThread(id)
  },
  { immediate: true },
)

/**
 * 为刚建出来的会话造一条列表项。
 *
 * 标题按后端同一条规则取首条提问的前 60 字。这是一份乐观副本，下一次 `load()` 会被服务端
 * 的真实数据替换掉；这么做是为了让新会话立刻出现在列表里，而不是等一次整页请求。
 * 截断长度与后端 `MAX_THREAD_TITLE_CHARS` 一致，改一边就要改另一边。
 */
function createdSummary(threadId: string): AgentThreadSummaryDto {
  const now = new Date().toISOString()
  const firstQuestion = chat.turns.value[0]?.question ?? ''
  return {
    thread_id: threadId,
    title: firstQuestion.split(/\s+/).join(' ').slice(0, 60) || '未命名会话',
    created_at: now,
    last_active_at: now,
  }
}

/** 点列表里的一项：只改 URL，载入由上面那个 watch 统一负责。 */
function openThread(threadId: string): void {
  if (threadId === routeThreadId.value) return
  void router.push({ name: 'agent-thread', params: { threadId } })
}

function startNewConversation(): void {
  chat.startNewConversation()
  if (routeThreadId.value !== null) void router.push({ name: 'agent-chat' })
}

// 有新一轮时把视口带到底部。只在轮数变化时滚动，不跟着每个 token 滚——逐 token 滚动会
// 抢走用户往上翻看历史的操作。
watch(
  () => chat.turns.value.length,
  async () => {
    await nextTick()
    transcriptEndRef.value?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  },
)

async function chooseExample(value: string): Promise<void> {
  chat.draft.value = value
  await chat.send()
}
</script>

<template>
  <!-- 「模型生成、只读检索」的语义不再由顶栏 mode-note 承担：输入区下方的 dock-note
       （回答由模型生成，可能有误；点击引用核对）说的就是这件事，一处就够。
       会话列表从正文里挪进外壳侧栏的 #rail——那是导航，不是内容。 -->
  <AppShell
    active="agent"
    main-id="agent-workspace"
    skip-label="跳到对话工作台"
    primary-label="新对话"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @primary="startNewConversation"
    @logout="logout"
  >
    <template #rail>
      <ThreadSidebar
        class="thread-rail"
        :threads="threadList.threads.value"
        :total="threadList.total.value"
        :active-thread-id="chat.threadId.value"
        :list-state="threadList.listState.value"
        :list-error="threadList.listError.value"
        :has-more="threadList.hasMore.value"
        :has-previous="threadList.hasPrevious.value"
        :is-empty="threadList.isEmpty.value"
        :deleting-thread-ids="threadList.deletingThreadIds.value"
        @open="openThread"
        @remove="threadList.remove"
        @reload="threadList.load"
        @next-page="threadList.nextPage"
        @previous-page="threadList.previousPage"
      />
    </template>

    <main id="agent-workspace" class="workspace">
      <div class="chat-column">
        <!-- 空态时这一格在剩余高度里居中（问候主角 + 建议卡）；
             有历史时它从顶部开始正常流动。切换在 .is-empty 上。 -->
        <div class="transcript-region" :class="{ 'is-empty': !hasHistory }">
          <p v-if="chat.isLoadingThread.value" class="thread-state" aria-live="polite">
            正在读取这个会话的历史…
          </p>

          <!-- 打不开某个会话时说明情况并给出下一步。不跳回 /agent：那样地址悄悄变了，
               用户不知道自己点的那个会话到底怎么了。 -->
          <BaseCallout
            v-else-if="chat.threadError.value"
            class="thread-error"
            tone="danger"
            :title="chat.threadError.value.title"
            :description="chat.threadError.value.description"
          />

          <!-- 历史被压缩过就如实说明。不说的话用户会以为看到的是全部记录，而模型实际上
               只记得一段摘要——两边对不上时，他会以为模型在胡说。 -->
          <BaseCallout
            v-if="chat.isHistoryTruncated.value"
            class="history-note"
            tone="neutral"
            description="较早消息已压缩，原始问答不再提供回看。近期保留的问答仍可查看。"
          >
            <template #icon><History :size="14" aria-hidden="true" /></template>
          </BaseCallout>

          <details
            v-if="chat.isHistoryTruncated.value && chat.historySummary.value"
            class="summary-background"
          >
            <summary>查看背景摘要</summary>
            <p>摘要仅作背景，不是可核验的原文引用。</p>
            <p class="summary-text">{{ chat.historySummary.value }}</p>
          </details>
          <BaseCallout
            v-if="chat.historySyncError.value"
            tone="neutral"
            :description="chat.historySyncError.value"
          >
            <template #actions
              ><BaseButton
                variant="outline"
                :disabled="chat.isStreaming.value"
                @click="chat.synchronizeHistory()"
                >同步会话状态</BaseButton
              ></template
            >
          </BaseCallout>

          <AgentTranscript
            :turns="chat.turns.value"
            :streaming="chat.isStreaming.value"
            :examples="AGENT_EXAMPLES"
            @retry="chat.retry"
            @choose-example="chooseExample"
            @open-evidence="openEvidence"
          />
          <!-- 滚动锚点。滚 transcript 本身会把它的顶部带进视口，方向正好相反。 -->
          <div ref="transcriptEndRef" class="scroll-anchor" aria-hidden="true"></div>
        </div>

        <div class="composer-dock" :class="{ 'has-history': hasHistory }">
          <AgentComposer
            v-model="chat.draft.value"
            :custom-prompt-active="preferences.agentSystemPrompt.trim().length > 0"
            :input-error="chat.inputError.value"
            :remaining-characters="chat.remainingCharacters.value"
            :streaming="chat.isStreaming.value"
            :can-send="chat.canSend.value"
            @submit="chat.send"
            @cancel="chat.cancel"
          >
            <template #scope>
              <KnowledgeBaseScopePicker
                v-if="!chat.isLoadingThread.value"
                :model-value="chat.selection.value"
                :knowledge-bases="scope.knowledgeBases.value"
                :loading="scope.loading.value"
                :error="scope.error.value || chat.scopeSaveError.value"
                @update:model-value="chat.updateSelection"
                @refresh="scope.refresh"
              />
              <span v-if="chat.savingScope.value" class="scope-note" role="status">
                正在保存会话范围…
              </span>
              <span v-else-if="chat.isStreaming.value" class="scope-note">
                现在改选只影响下一次提问。
              </span>
            </template>
          </AgentComposer>
          <p class="dock-note">
            <ShieldCheck :size="14" aria-hidden="true" />
            回答由模型生成，可能有误；点击引用核对资料与当前原文。
          </p>
        </div>
      </div>
    </main>
  </AppShell>
  <DocumentReader
    :open="reader.isOpen.value"
    :result="reader.selectedResult.value"
    :detail="reader.detail.value"
    :loading="reader.isLoading.value"
    :error="reader.error.value"
    :hash-mismatch="reader.contentHashMismatch.value"
    :evidence="selectedEvidence"
    @close="reader.close"
    @closed="reader.restoreFocus"
    @retry="reader.retry"
  />
</template>

<style scoped>
/* 会话列表已挪进外壳侧栏，正文回到单列居中阅读列（Q1/Q2 定的单列阅读）。
   阅读列按 --reading-width 收窄：单列正文超过 ~76ch 眼睛就要来回扫。 */

.workspace {
  display: flex;
  flex-direction: column;
  width: min(calc(100% - 40px), var(--reading-width));
  margin: 0 auto;
  /* 正好占满视口减汉堡条：多了会凭空多出一条滚动，少了输入区浮在半空。
     --app-header-offset 由 AppShell 发布（桌面 0px，窄屏 57px）。 */
  min-height: calc(100vh - var(--app-header-offset, 0px));
  /* 动态视口高度，避开移动端浏览器地址栏收起时 100vh 偏大导致底部被切。
     两条都写，dvh 不支持时退回上面那条。 */
  min-height: calc(100dvh - var(--app-header-offset, 0px));
}

.chat-column {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: inherit;
}

.thread-state {
  padding: 6px 2px 12px;
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
}

/* 面板本体归 BaseCallout；这里只留节奏。 */
.thread-error {
  margin-bottom: 14px;
}

.history-note {
  margin-bottom: 14px;
}

.summary-background {
  margin: 0 0 18px;
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: 1.7;
}
.summary-background summary {
  cursor: pointer;
}
.summary-background p {
  margin-top: 10px;
}
.summary-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.scope-note {
  display: inline-flex;
  align-items: center;
  margin: 0 4px;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

/* flex: 1 是为了空态那条 justify-content: flex-end 能生效——不占满剩余高度，
   「压到底部」就无从谈起。记录变长时整列超过 min-height 往下长，由文档滚动承接。 */
.transcript-region {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  padding-top: 26px;
}

/* 空态在剩余高度里居中（与检索页空态同一形态）：问候是主角，建议卡陪衬。
   有历史时不居中：第一轮从顶部开始正常流动。 */
.transcript-region.is-empty {
  justify-content: center;
  padding-bottom: 8px;
}

/* 矮视口的空态从顶部排列，超出内容交给文档滚动。 */
@media (max-width: 560px), (max-height: 700px) {
  .transcript-region.is-empty {
    justify-content: flex-start;
  }
}

/* 高度为 0 的锚点：它只用来给 scrollIntoView 一个落点，不占布局。
   flex: 0 0 auto 拦住 flex 容器给它分配高度。 */
.scroll-anchor {
  flex: 0 0 auto;
  height: 0;
}

/* 开始会话后输入区贴底，空态保持正常流向，避免遮住尚未点击的建议。
   sticky 留在文档流里，记录区不需要额外预留输入区高度。
   顶部那道渐变是让滚上来的内容在贴近输入区时淡出，而不是被一条硬边裁断。 */
.composer-dock {
  padding: 12px 0 10px;
  background: linear-gradient(to bottom, transparent, var(--surface-base) 22%);
}

.composer-dock.has-history {
  position: sticky;
  z-index: var(--z-dock);
  bottom: 0;
}

.dock-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 4px 0;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.dock-note svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--accent);
}

@media (max-width: 900px) {
  .chat-column {
    min-height: 0;
  }
}

@media (max-width: 560px) {
  .workspace {
    width: calc(100% - 24px);
  }

  .transcript-region {
    padding-top: 18px;
  }

  .composer-dock {
    padding: 10px 0 8px;
  }
}
</style>
