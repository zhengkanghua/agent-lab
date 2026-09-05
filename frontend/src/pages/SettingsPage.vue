<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ShieldCheck } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { authSession, useLogout } from '@/features/auth'
import {
  AccountSection,
  AgentPromptSection,
  SearchPreferencesSection,
  SettingsNav,
  type SettingsSection,
} from '@/features/settings'

/**
 * 设置中心：账号安全、检索偏好、Agent 偏好（超管）都在这里。
 *
 * 分区由路由参数决定（/settings/search 可刷新、可收藏，和 /agent/:threadId 同一个
 * 取舍），分区组件按需渲染。Agent 偏好分区只对超级用户有意义——后端 /agent/* 只放行
 * 超管，普通用户编辑了提示词也没有地方生效；路由守卫会把这来访客送回账号分区。
 */
const route = useRoute()
const router = useRouter()

const { loggingOut, logoutError, logout } = useLogout()

const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)

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
</script>

<template>
  <!-- 账号页原本不渲染页脚，那是单屏表单页的取舍；设置中心是内容页，页脚回来。 -->
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="新闻语义研究台"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="settings-page"
    skip-label="跳到设置内容"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><ShieldCheck :size="19" stroke-width="2.2" /></template>

    <!-- 设置页是「深入」页面，没有显式的回去入口时，用户会把最右边的退出键当成
         返回用（2026-09 老板实测点退出登出了）。给一个明确标签的返回按钮。 -->
    <template #nav>
      <BaseButton variant="ghost" size="sm" :to="{ name: 'search' }">
        <template #icon><ArrowLeft :size="15" aria-hidden="true" /></template>
        返回工作台
      </BaseButton>
    </template>

    <main id="settings-page" class="settings-page">
      <h1 class="sr-only">设置中心</h1>
      <div class="settings-layout">
        <SettingsNav class="settings-rail" :section="section" :is-superuser="isSuperuser" />

        <div class="settings-content">
          <AccountSection v-if="section === 'account'" :user="authSession.user.value" />
          <SearchPreferencesSection v-else-if="section === 'search'" />
          <AgentPromptSection v-else-if="section === 'agent' && isSuperuser" />
        </div>
      </div>
    </main>
  </AppShell>
</template>

<style scoped>
.settings-page {
  width: min(calc(100% - 48px), 960px);
  margin: 0 auto;
  padding: var(--space-6) 0 var(--space-8);
}

/* 左导航右内容：商业设置页的标准两栏。左栏自适应内容宽、sticky 跟随滚动，
   右栏吃掉剩余宽度。 */
.settings-layout {
  display: grid;
  grid-template-columns: 224px minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

.settings-rail {
  position: sticky;
  top: calc(var(--app-topbar-height, 69px) + 16px);
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
