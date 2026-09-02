<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bot, History, Search, ShieldCheck } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { useLogout } from '@/features/auth/useLogout'
import AgentComposer from '@/features/agent-chat/components/AgentComposer.vue'
import AgentTranscript from '@/features/agent-chat/components/AgentTranscript.vue'
import ThreadSidebar from '@/features/agent-chat/components/ThreadSidebar.vue'
import { useAgentChat } from '@/features/agent-chat/composables/useAgentChat'
import { useAgentDefaultPrompt } from '@/features/agent-chat/composables/useAgentDefaultPrompt'
import { useThreadList } from '@/features/agent-chat/composables/useThreadList'
import { AGENT_EXAMPLES } from '@/features/agent-chat/constants/examples'
import type { AgentThreadSummaryDto } from '@/api/agent-threads'

const route = useRoute()
const router = useRouter()

const chat = useAgentChat()
const { defaultPrompt, load: loadDefaultPrompt } = useAgentDefaultPrompt()

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

/* 前台顶栏只放「语义检索」这一功能跳转。后台入口统一收敛到账号设置页，不在前台顶栏
   重复放图标——能进本页的已是超管，需要管理账号时从右上角账号设置进入。 */
const navLinks = computed(() => [{ to: { name: 'search' }, label: '语义检索', icon: Search }])

/** 路由参数里的会话 id。`/agent` 上没有这个参数，值为 null。 */
const routeThreadId = computed(() => {
  const value = route.params.threadId
  const id = Array.isArray(value) ? value[0] : value
  return id ? String(id) : null
})

onMounted(() => {
  void loadDefaultPrompt()
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
    if (id === null) {
      // 从某个会话回到 /agent（点「新对话」或后退）时清空，否则旧会话的历史留在界面上，
      // 而 threadId 已经没了，下一轮会开一个新会话。
      if (chat.threadId.value !== null) chat.startNewConversation()
      return
    }
    if (id === chat.threadId.value) return
    void chat.loadThread(id)
  },
  { immediate: true },
)

/*
 * 服务端新建会话后把 URL 补上，并把新会话并进列表。
 *
 * 用 replace 而不是 push：这一步是「补全当前所在位置的地址」，不是一次导航。push 会让
 * 后退键先回到 /agent（同一段对话、但地址上没有 id），点两次才真正离开。
 */
watch(
  () => chat.threadId.value,
  (id, previous) => {
    if (id === null || id === previous) return
    if (routeThreadId.value === id) return
    void router.replace({ name: 'agent-thread', params: { threadId: id } })
    threadList.acceptCreatedThread(createdSummary(id))
  },
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
  if (threadId === chat.threadId.value) return
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
  <!-- 这一页不传 footerBrand，所以外壳不渲染页脚。理由是 Q2 定的「底部固定输入区」
       与页脚互斥：页脚只能落在输入区下方，用户要多滚一屏才能看到一行装饰性文字，
       而输入区本来就该是这一列的最后一个元素。页脚那句「只读访问」由顶栏的
       mode-note 承担，「模型生成」由输入区下方的细则行承担，信息没有丢。 -->
  <AppShell
    brand-title="Signal Desk Agent"
    brand-subtitle="会自己查资料的新闻助手"
    brand-label="返回检索工作台"
    :brand-to="{ name: 'search' }"
    main-id="agent-workspace"
    skip-label="跳到对话工作台"
    :nav-links="navLinks"
    mode-label="模型生成答案"
    mode-detail="只读检索"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Bot :size="19" stroke-width="2.2" /></template>

    <main id="agent-workspace" class="workspace">
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
        @new-conversation="startNewConversation"
      />

      <div class="chat-column">
        <!-- 空态时这一格靠 justify-content 把内容压到底部，紧贴输入区；
             有历史时它从顶部开始正常流动。切换在 .is-empty 上。 -->
        <div class="transcript-region" :class="{ 'is-empty': !hasHistory }">
          <p v-if="chat.isLoadingThread.value" class="thread-state" aria-live="polite">
            正在读取这个会话的历史…
          </p>

          <!-- 打不开某个会话时说明情况并给出下一步。不跳回 /agent：那样地址悄悄变了，
               用户不知道自己点的那个会话到底怎么了。 -->
          <div v-else-if="chat.threadError.value" class="thread-error" role="alert">
            <p class="thread-error-title">{{ chat.threadError.value.title }}</p>
            <p class="thread-error-description">{{ chat.threadError.value.description }}</p>
          </div>

          <!-- 历史被压缩过就如实说明。不说的话用户会以为看到的是全部记录，而模型实际上
               只记得一段摘要——两边对不上时，他会以为模型在胡说。 -->
          <p v-if="chat.isHistoryTruncated.value" class="history-note">
            <History :size="14" aria-hidden="true" />
            较早的对话已被压缩成摘要，这里只显示保留下来的轮次。
          </p>

          <AgentTranscript
            :turns="chat.turns.value"
            :streaming="chat.isStreaming.value"
            :examples="AGENT_EXAMPLES"
            @retry="chat.retry"
            @choose-example="chooseExample"
          />
          <!-- 滚动锚点。滚 transcript 本身会把它的顶部带进视口，方向正好相反。 -->
          <div ref="transcriptEndRef" class="scroll-anchor" aria-hidden="true"></div>
        </div>

        <div class="composer-dock">
          <AgentComposer
            v-model="chat.draft.value"
            :system-prompt="chat.systemPrompt.value"
            :default-prompt="defaultPrompt"
            :input-error="chat.inputError.value"
            :remaining-characters="chat.remainingCharacters.value"
            :streaming="chat.isStreaming.value"
            :can-send="chat.canSend.value"
            :has-history="hasHistory"
            @update:system-prompt="chat.systemPrompt.value = $event"
            @submit="chat.send"
            @cancel="chat.cancel"
            @new-conversation="startNewConversation"
          />
          <p class="dock-note">
            <ShieldCheck :size="14" aria-hidden="true" />
            回答由模型生成，可能有误；请按它给出的来源核对原文。它只读数据，不改新闻与索引。
          </p>
        </div>
      </div>
    </main>
  </AppShell>
</template>

<style scoped>
/* 会话导轨 + 居中阅读列。阅读列的宽度与居中位置保持原样（Q1/Q2 定的单列阅读），
   导轨挂在它左边而不是挤占它：正文宽度是排版决定，不该因为多了个列表就变窄。 */

.workspace {
  display: grid;
  /* 左轨定宽、右侧 1fr，然后整体在页面里居中。用 grid 而不是 flex：
     导轨要能 sticky 在自己那一列里，flex 子项拉伸后 sticky 的参照高度会变成整列。 */
  grid-template-columns: 244px minmax(0, 1fr);
  gap: 20px;
  width: min(calc(100% - 40px), var(--content-width));
  margin: 0 auto;
  /* 正好占满视口减顶栏：多了会凭空多出一条滚动，少了输入区浮在半空。
     --app-topbar-height 由 AppShell 提供，两个 compact 断点会改写它。 */
  min-height: calc(100vh - var(--app-topbar-height, 69px));
  /* 动态视口高度，避开移动端浏览器地址栏收起时 100vh 偏大导致底部被切。
     两条都写，dvh 不支持时退回上面那条。 */
  min-height: calc(100dvh - var(--app-topbar-height, 69px));
}

.thread-rail {
  margin-top: 26px;
  /* 与 .transcript-region 的 padding-top 对齐，让列表首项和第一轮问答齐头。 */
}

.chat-column {
  display: flex;
  flex-direction: column;
  /* 阅读列比检索页窄得多：--content-width 是 1420px，那是给两栏结果用的。
     单列正文超过 ~76ch 眼睛就要来回扫，这里按 --reading-width 收窄。 */
  width: min(100%, var(--reading-width));
  min-height: inherit;
  /* 在自己那一格里居中，而不是靠外层：外层已经被导轨占掉一列了。 */
  margin: 0 auto;
}

.thread-state {
  padding: 6px 2px 12px;
  color: var(--text-muted);
  font-size: 0.85rem;
}

.thread-error {
  margin-bottom: 14px;
  padding: 12px 13px;
  border: 1px solid var(--danger-soft);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
}

.thread-error-title {
  color: var(--danger);
  font-size: 0.85rem;
  font-weight: 720;
}

.thread-error-description {
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: 0.8rem;
  line-height: 1.6;
}

/* 压缩说明用中性色，不用警告色：这不是故障，是正常的上下文管理。
   用警告色会让人以为出了问题。 */
.history-note {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 14px;
  padding: 8px 11px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  background: var(--surface-sunken);
  font-size: 0.75rem;
  line-height: 1.55;
}

.history-note svg {
  flex: 0 0 auto;
}

/* flex: 1 是为了空态那条 justify-content: flex-end 能生效——不占满剩余高度，
   「压到底部」就无从谈起。记录变长时整列超过 min-height 往下长，由文档滚动承接。 */
.transcript-region {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  padding-top: 26px;
}

/* 空态把内容推到底部，让标题与建议卡紧贴输入区——那是视线落点。
   有历史时不这么做：那时第一轮该从顶部开始。 */
.transcript-region.is-empty {
  justify-content: flex-end;
  padding-bottom: 8px;
}

/* 高度为 0 的锚点：它只用来给 scrollIntoView 一个落点，不占布局。
   flex: 0 0 auto 拦住 flex 容器给它分配高度。 */
.scroll-anchor {
  flex: 0 0 auto;
  height: 0;
}

/* 输入区贴底。用 sticky 而不是 fixed：sticky 留在文档流里，所以上面的记录区
   不需要用 padding 给它腾位置，也不会在 iOS 上跟着软键盘乱跳。
   顶部那道渐变是让滚上来的内容在贴近输入区时淡出，而不是被一条硬边裁断。 */
.composer-dock {
  position: sticky;
  z-index: 5;
  bottom: 0;
  padding: 12px 0 10px;
  background: linear-gradient(to bottom, transparent, var(--surface-base) 22%);
}

.dock-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 4px 0;
  color: var(--text-secondary);
  font-size: 0.7rem;
  line-height: 1.5;
}

.dock-note svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--accent);
}

/* 窄屏收成一列：导轨排到对话上方。放在下面会让人以为它是页脚的一部分，
   而它是导航——第一屏就该看得见。 */
@media (max-width: 900px) {
  .workspace {
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
  }

  .thread-rail {
    margin-top: 18px;
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
