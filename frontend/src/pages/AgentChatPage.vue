<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Bot, Search, ShieldCheck } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import { useLogout } from '@/features/auth/useLogout'
import AgentComposer from '@/features/agent-chat/components/AgentComposer.vue'
import AgentTranscript from '@/features/agent-chat/components/AgentTranscript.vue'
import { useAgentChat } from '@/features/agent-chat/composables/useAgentChat'
import { useAgentDefaultPrompt } from '@/features/agent-chat/composables/useAgentDefaultPrompt'

const EXAMPLES = [
  '最近有哪些关于利率的报道？',
  '总结一下本周的科技新闻要点',
  '关于新能源汽车，各家来源的说法有分歧吗？',
] as const

const chat = useAgentChat()
const { defaultPrompt, load: loadDefaultPrompt } = useAgentDefaultPrompt()

// beforeLogout 里掐掉在途的流：留着它会在退出后继续读一条已经没有权限的连接。
const { loggingOut, logoutError, logout } = useLogout({ beforeLogout: chat.cancel })

const transcriptEndRef = ref<HTMLElement | null>(null)

const hasHistory = computed(() => chat.turns.value.length > 0)

const navLinks = [{ to: { name: 'search' }, label: '语义检索', icon: Search }]

onMounted(() => {
  void loadDefaultPrompt()
})

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
      <div class="chat-column">
        <!-- 空态时这一格靠 justify-content 把内容压到底部，紧贴输入区；
             有历史时它从顶部开始正常流动。切换在 .is-empty 上。 -->
        <div class="transcript-region" :class="{ 'is-empty': !hasHistory }">
          <AgentTranscript
            :turns="chat.turns.value"
            :streaming="chat.isStreaming.value"
            :examples="EXAMPLES"
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
            @new-conversation="chat.startNewConversation"
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
/* 单列居中（Q1/Q2）。原来是「左侧 sticky 输入栏 + 右侧记录」的两栏，
   现在整页一条阅读列，输入区贴底。 */

.workspace {
  /* 正好占满视口减顶栏：多了会凭空多出一条滚动，少了输入区浮在半空。
     --app-topbar-height 由 AppShell 提供，两个 compact 断点会改写它。 */
  min-height: calc(100vh - var(--app-topbar-height, 69px));
  /* 动态视口高度，避开移动端浏览器地址栏收起时 100vh 偏大导致底部被切。
     两条都写，dvh 不支持时退回上面那条。 */
  min-height: calc(100dvh - var(--app-topbar-height, 69px));
}

.chat-column {
  display: flex;
  flex-direction: column;
  /* 阅读列比检索页窄得多：--content-width 是 1420px，那是给两栏结果用的。
     单列正文超过 ~76ch 眼睛就要来回扫，这里按 --reading-width 收窄。 */
  width: min(calc(100% - 40px), var(--reading-width));
  min-height: inherit;
  margin: 0 auto;
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

@media (max-width: 560px) {
  .chat-column {
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
