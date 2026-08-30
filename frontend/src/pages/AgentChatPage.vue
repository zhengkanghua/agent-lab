<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Bot, LogOut, Search, UserRound } from '@lucide/vue'
import { useRouter } from 'vue-router'
import { queryClient } from '../app/query-client'
import { authSession } from '../features/auth/auth-session'
import AgentComposer from '../features/agent-chat/components/AgentComposer.vue'
import AgentTranscript from '../features/agent-chat/components/AgentTranscript.vue'
import { useAgentChat } from '../features/agent-chat/composables/useAgentChat'
import { useAgentDefaultPrompt } from '../features/agent-chat/composables/useAgentDefaultPrompt'

const EXAMPLES = [
  '最近有哪些关于利率的报道？',
  '总结一下本周的科技新闻要点',
  '关于新能源汽车，各家来源的说法有分歧吗？',
] as const

const router = useRouter()
const chat = useAgentChat()
const { defaultPrompt, load: loadDefaultPrompt } = useAgentDefaultPrompt()

const transcriptRef = ref<HTMLElement | null>(null)
const loggingOut = ref(false)
const logoutError = ref(false)

const hasHistory = computed(() => chat.turns.value.length > 0)

onMounted(() => {
  void loadDefaultPrompt()
})

// 有新一轮时把视口带到底部。只在轮数变化时滚动，不跟着每个 token 滚——逐 token 滚动会
// 抢走用户往上翻看历史的操作。
watch(
  () => chat.turns.value.length,
  async () => {
    await nextTick()
    transcriptRef.value?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  },
)

async function chooseExample(value: string): Promise<void> {
  chat.draft.value = value
  await chat.send()
}

async function logout(): Promise<void> {
  if (loggingOut.value) return

  loggingOut.value = true
  logoutError.value = false
  try {
    // 先掐掉在途的流：留着它会在退出后继续读一条已经没有权限的连接。
    chat.cancel()
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
    <a class="skip-link" href="#agent-workspace">跳到对话工作台</a>

    <header class="topbar">
      <div class="content-wrap topbar-inner">
        <RouterLink class="brand-lockup" :to="{ name: 'search' }" aria-label="返回检索工作台">
          <span class="brand-mark" aria-hidden="true">
            <Bot :size="19" stroke-width="2.2" />
          </span>
          <span class="brand-copy">
            <strong>Signal Desk Agent</strong>
            <small>会自己查资料的新闻助手</small>
          </span>
        </RouterLink>

        <div class="topbar-actions">
          <div class="mode-note">
            <span class="mode-dot" aria-hidden="true"></span>
            <span>模型生成答案</span>
            <span class="mode-detail">只读检索</span>
          </div>

          <div class="account-control">
            <RouterLink
              class="admin-link"
              :to="{ name: 'search' }"
              aria-label="回到语义检索"
              title="语义检索"
            >
              <Search :size="17" aria-hidden="true" />
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

    <main id="agent-workspace" class="content-wrap workspace">
      <aside class="composer-pane" aria-labelledby="page-title">
        <div class="workspace-intro">
          <p class="workspace-label">Agent 工作台</p>
          <h1 id="page-title">
            <span>让它先查证，</span>
            <span>再给你答案。</span>
          </h1>
          <p>
            提出问题，Agent 会自行决定检索哪些新闻、要不要读全文，然后基于查到的内容作答并给出来源。
          </p>
        </div>

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
      </aside>

      <div ref="transcriptRef" class="transcript-pane">
        <AgentTranscript
          :turns="chat.turns.value"
          :streaming="chat.isStreaming.value"
          :examples="EXAMPLES"
          @retry="chat.retry"
          @choose-example="chooseExample"
        />
      </div>
    </main>

    <footer class="site-footer">
      <div class="content-wrap footer-inner">
        <span>Signal Desk Agent</span>
        <span>模型生成 · 工具调用可见 · 只读访问</span>
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

/* .topbar-inner、.brand-mark、.brand-copy 见 styles/components/topbar.css。 */

.brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

.brand-copy strong {
  font-family: var(--display-font);
  font-size: 1rem;
  font-weight: 760;
  line-height: 1.2;
}

.brand-copy small {
  color: var(--ink-700);
  font-size: 0.72rem;
}

.mode-note {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-700);
  font-size: 0.78rem;
  white-space: nowrap;
}

.mode-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--signal-500);
  box-shadow: 0 0 0 4px var(--source-100);
}

.mode-detail {
  padding-left: 8px;
  border-left: 1px solid var(--paper-300);
  color: var(--ink-500);
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

.workspace {
  display: grid;
  grid-template-columns: minmax(310px, 400px) minmax(0, 1fr);
  align-items: start;
  gap: 34px;
  min-height: calc(100vh - 126px);
  padding-top: 34px;
  padding-bottom: 64px;
}

.composer-pane {
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
  font-size: 2.1rem;
  font-weight: 780;
  line-height: 1.16;
}

.workspace-intro h1 span {
  display: block;
}

.workspace-intro > p:not(.workspace-label) {
  max-width: 36ch;
  margin-top: 16px;
  color: var(--ink-700);
  font-size: 0.92rem;
  line-height: 1.72;
}

.transcript-pane {
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
    gap: 30px;
    padding-top: 30px;
  }

  .composer-pane {
    position: static;
  }

  .workspace-intro h1,
  .workspace-intro > p:not(.workspace-label) {
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
    gap: 26px;
    padding-top: 24px;
    padding-bottom: 46px;
  }

  .workspace-intro {
    padding-bottom: 22px;
  }

  .workspace-intro h1 {
    max-width: none;
    font-size: 1.85rem;
  }
}
</style>
