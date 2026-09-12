<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { X } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import { authSession, useLogout } from '@/features/auth'
import {
  AccountSection,
  AgentPromptSection,
  SearchPreferencesSection,
  SettingsNav,
  usePreferences,
  type SettingsSection,
} from '@/features/settings'

/**
 * 设置中心：账号安全、检索偏好、Agent 偏好（超管）都在这里。
 *
 * 桌面端是外壳内容区上的居中浮层（2026-09 重设计 P4）：路由与深链不变，
 * /settings/search 等地址仍然直达；侧栏在浮层之外保持可点，导航离开就是出口。
 * 窄屏（≤720px）回退整页形态——没有遮罩与 Esc，dialog 语义一并撤掉。
 * 分区由路由参数决定，分区组件按需渲染。Agent 偏好分区只对超级用户有意义。
 */
const route = useRoute()
const router = useRouter()

const { loggingOut, logoutError, logout } = useLogout()

const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)
const { preferences } = usePreferences()
const agentPromptDraft = ref(preferences.agentSystemPrompt)
const hasUnsavedPrompt = computed(
  () => isSuperuser.value && agentPromptDraft.value !== preferences.agentSystemPrompt,
)

function confirmDiscardPrompt(): boolean {
  return !hasUnsavedPrompt.value || window.confirm('提示词尚未保存，确定离开并放弃修改？')
}

onBeforeRouteLeave(confirmDiscardPrompt)

async function requestLogout(): Promise<void> {
  if (confirmDiscardPrompt()) await logout()
}

function warnBeforeUnload(event: BeforeUnloadEvent): void {
  if (!hasUnsavedPrompt.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onScopeDispose(() => window.removeEventListener('beforeunload', warnBeforeUnload))

const SECTION_KEYS: readonly SettingsSection[] = ['account', 'search', 'agent']

const section = computed<SettingsSection>(() => {
  const value = route.params.section
  const key = Array.isArray(value) ? value[0] : value
  return SECTION_KEYS.includes(key as SettingsSection) ? (key as SettingsSection) : 'account'
})

// 非法分区不换 URL 静默吞掉：地址栏还是 /settings/whatever，界面上却是账号分区，
// 收藏与分享会落空。重定向到真实分区，地址与内容对齐。
watch(
  section,
  (value) => {
    if (route.params.section !== value) {
      void router.replace({ name: 'settings', params: { section: value } })
    }
  },
  { immediate: true },
)

// 普通用户手输 /settings/agent：守卫已拦一层，这里兜底（守卫改动落后于组件渲染的窗口）。
watch(
  () => [section.value, isSuperuser.value] as const,
  ([current, superuser]) => {
    if (current === 'agent' && !superuser) {
      void router.replace({ name: 'settings', params: { section: 'account' } })
    }
  },
  { immediate: true },
)

watch(
  () => route.fullPath,
  () => {
    window.scrollTo({ top: 0 })
  },
)

/* 浮层与整页的切换（同外壳抽屉的 innerWidth 检测）。 */
const isFloating = ref(window.innerWidth > 720)

function updateViewport(): void {
  isFloating.value = window.innerWidth > 720
}

const panelRef = ref<HTMLElement | null>(null)

/** 关闭浮层：有来路就回上一页（多半是从检索页的偏好入口进来的），否则回工作台。
 *  离开确认不在这里做——onBeforeRouteLeave 守卫统一拦，Esc 和侧栏导航走同一条路。 */
function closeSettings(): void {
  const historyState = router.options.history.state as { back?: string | null }
  if (historyState.back != null) router.back()
  else void router.push({ name: 'search' })
}

/** 桌面端浮层点击外部半透明遮罩关闭。 */
function onOverlayClick(): void {
  if (isFloating.value) {
    closeSettings()
  }
}

/* Esc 关闭与 Tab 循环只在浮层形态生效（模式与外壳抽屉一致）。 */
function onPanelKeydown(event: KeyboardEvent): void {
  if (!isFloating.value) return
  if (event.key === 'Escape') {
    event.stopPropagation()
    closeSettings()
    return
  }
  if (event.key !== 'Tab') return
  const focusables = panelRef.value?.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusables || focusables.length === 0) return
  const first = focusables[0]!
  const last = focusables[focusables.length - 1]!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

onMounted(() => {
  window.addEventListener('resize', updateViewport)
  if (isFloating.value) panelRef.value?.focus()
})
onScopeDispose(() => window.removeEventListener('resize', updateViewport))
</script>

<template>
  <AppShell
    active="settings"
    main-id="settings-page"
    skip-label="跳到设置内容"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="requestLogout"
  >
    <main id="settings-page" class="settings-page">
      <div
        class="settings-overlay"
        :class="{ 'is-floating': isFloating }"
        @click.self="onOverlayClick"
      >
        <section
          ref="panelRef"
          class="settings-panel"
          :role="isFloating ? 'dialog' : undefined"
          :aria-modal="isFloating ? 'true' : undefined"
          aria-label="设置中心"
          :tabindex="isFloating ? -1 : undefined"
          @keydown="onPanelKeydown"
        >
          <header class="settings-heading">
            <h1>设置中心</h1>
            <BaseIconButton
              v-if="isFloating"
              class="panel-close"
              label="关闭设置"
              size="sm"
              @click="closeSettings"
            >
              <X :size="17" aria-hidden="true" />
            </BaseIconButton>
          </header>

          <div class="settings-layout">
            <SettingsNav class="settings-rail" :section="section" :is-superuser="isSuperuser" />

            <div class="settings-content">
              <AccountSection v-if="section === 'account'" :user="authSession.user.value" />
              <SearchPreferencesSection v-else-if="section === 'search'" />
              <AgentPromptSection
                v-else-if="section === 'agent' && isSuperuser"
                v-model="agentPromptDraft"
              />
            </div>
          </div>
        </section>
      </div>
    </main>
  </AppShell>
</template>

<style scoped>
/* 整页形态（≤720px 回退）：内容居中收窄，遮罩与吸附一概没有。 */
.settings-page {
  width: min(calc(100% - 48px), 960px);
  margin: 0 auto;
  padding: var(--space-6) 0 var(--space-8);
}

/* 桌面浮层：盖在内容区上。侧栏的 z 层更高（--z-drawer-sidebar 40 > 35），
   遮罩盖不住它——侧栏导航是设置的第二条合法出口，不该被挡。 */
.settings-overlay.is-floating {
  position: fixed;
  inset: 0;
  z-index: var(--z-drawer-overlay);
  display: grid;
  place-items: center;
  padding: var(--space-6);
  background: var(--surface-overlay);
}

.settings-panel {
  min-width: 0;
}

.settings-overlay.is-floating .settings-panel {
  display: flex;
  flex-direction: column;
  width: min(720px, 100%);
  max-height: calc(100dvh - var(--space-6) * 2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  overflow: hidden;
}

/* 焦点由面板容器持有（tabindex=-1），容器自己不画环：环是给键盘用户的控件提示，
   面板整体不是控件。 */
.settings-panel:focus {
  outline: none;
}

.settings-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-subtle);
}

.settings-heading h1 {
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
}

/* 左导航右内容：商业设置页的标准两栏。内容超高时整个面板体滚动，
   左导航吸在滚动区顶部。 */
.settings-layout {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: var(--space-5);
  align-items: start;
}

.settings-overlay.is-floating .settings-layout {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: var(--space-5);
}

.settings-rail {
  position: sticky;
  top: calc(var(--app-header-offset, 0px) + 16px);
}

.settings-overlay.is-floating .settings-rail {
  top: 0;
}

.settings-content {
  min-width: 0;
}

/* 窄屏导航横排在上、内容在下。 */
@media (max-width: 720px) {
  .settings-page {
    width: calc(100% - 32px);
    padding: var(--space-4) 0 var(--space-8);
  }

  .settings-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
  }

  .settings-rail {
    position: static;
    margin: 0 calc(var(--space-4) * -1);
    padding: 2px var(--space-4);
  }
}
</style>
