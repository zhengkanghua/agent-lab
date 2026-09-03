<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeft, CalendarClock, LogOut, Menu, ShieldCheck, UsersRound, X } from '@lucide/vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { authSession, useLogout } from '@/features/auth'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'

/* 后台控制台布局：固定左侧导航 + 右侧内容区。它作为 /admin 父路由的组件挂在路由上，
 * 内容区用 <RouterView> 承载子页面（账号管理等）。
 *
 * 与 AppShell 的区别是刻意的：AppShell 是前台工作台的单条顶栏，AdminShell 是管理端的
 * 侧边栏布局，二者不互相归并，各自只服务一个领域。
 *
 * 后台导航在这里集中定义（侧边栏对后台所有页面一致），新增后台页面时：
 *   1) 在 router.ts 的 /admin.children 加一条（带 meta.title / meta.subtitle）；
 *   2) 在下方的 adminMenuItems 加一项。
 * 权限守卫挂在 /admin 父路由，新页面自动继承「后台只给超管」。
 * 退出登录归本外壳持有：顶栏的退出键在这里，退登成功后跳登录页会卸载当前子页面，
 * 子页里的敏感输入随之被回收，不必让每个子页面各自接线退登。
 */

const route = useRoute()

const headingTitle = computed(() => (route.meta.title as string | undefined) ?? '管理控制台')
const headingSubtitle = computed(() => route.meta.subtitle as string | undefined)

const adminMenuItems = [
  { to: { name: 'user-admin' }, label: '账号管理', icon: UsersRound },
  { to: { name: 'scheduled-jobs' }, label: '定时任务', icon: CalendarClock },
]

const { loggingOut, logoutError, logout } = useLogout()

/* 移动端抽屉：桌面常驻，窄屏收起为抽屉。 */
const drawerOpen = ref(false)
const previousScrollTop = ref(0)
function openDrawer(): void {
  previousScrollTop.value = window.scrollY
  drawerOpen.value = true
}
function closeDrawer(): void {
  drawerOpen.value = false
  window.scrollTo({ top: previousScrollTop.value })
}
</script>

<template>
  <div class="admin-shell">
    <a class="skip-link" href="#admin-content">跳到内容</a>

    <!-- 侧边栏：桌面常驻，窄屏变成抽屉。 -->
    <aside class="admin-sidebar" :class="{ 'is-open': drawerOpen }" aria-label="后台导航">
      <div class="sidebar-brand">
        <span class="sidebar-brand-mark" aria-hidden="true">
          <ShieldCheck :size="20" stroke-width="2.2" />
        </span>
        <span class="sidebar-brand-copy">
          <strong>Signal Desk</strong>
          <small>管理控制台</small>
        </span>
        <BaseIconButton class="sidebar-close" label="关闭导航" @click="closeDrawer">
          <X :size="18" aria-hidden="true" />
        </BaseIconButton>
      </div>

      <nav class="sidebar-nav">
        <RouterLink class="menu-back" :to="{ name: 'search' }" @click="closeDrawer">
          <ArrowLeft :size="16" aria-hidden="true" />
          <span>返回工作台</span>
        </RouterLink>

        <p class="menu-group-label">后台管理</p>

        <RouterLink
          v-for="item in adminMenuItems"
          :key="item.label"
          class="menu-item"
          :to="item.to"
          @click="closeDrawer"
        >
          <component :is="item.icon" :size="17" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </aside>

    <!-- 窄屏抽屉遮罩 -->
    <button
      v-if="drawerOpen"
      class="sidebar-overlay"
      aria-label="关闭导航"
      @click="closeDrawer"
    ></button>

    <!-- 右侧内容区 -->
    <div class="admin-main-wrap">
      <header class="admin-topbar">
        <BaseIconButton class="menu-toggle" label="打开导航" @click="openDrawer">
          <Menu :size="19" aria-hidden="true" />
        </BaseIconButton>

        <div class="topbar-heading">
          <p v-if="headingSubtitle" class="topbar-subtitle">{{ headingSubtitle }}</p>
          <h1 class="topbar-title" :title="headingTitle">{{ headingTitle }}</h1>
        </div>

        <div class="topbar-actions">
          <RouterLink
            v-if="authSession.user.value"
            :to="{ name: 'account' }"
            class="account-identity"
            :aria-label="`账号设置 - ${authSession.user.value.email}`"
            :title="`账号设置 - ${authSession.user.value.email}`"
          >
            <span>{{ authSession.user.value.email }}</span>
          </RouterLink>

          <BaseIconButton label="退出登录" busy-cursor :disabled="loggingOut" @click="logout">
            <LogOut :size="17" aria-hidden="true" />
          </BaseIconButton>

          <span v-if="logoutError" class="logout-error" role="alert">退出失败</span>
        </div>
      </header>

      <main id="admin-content" class="admin-content">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  min-height: 100vh;
}

/* 侧边栏固定贴左，全高。内容区用 margin-left 让位，宽度交给 flex。 */
.admin-sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 40;
  display: flex;
  flex-direction: column;
  width: 244px;
  background: var(--surface-raised);
  border-right: 1px solid var(--border-subtle);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 64px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-subtle);
}

.sidebar-brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  border-radius: var(--radius-sm);
  color: var(--text-on-inverse);
  background: var(--surface-inverse);
  box-shadow: inset 4px 0 var(--accent);
}

.sidebar-brand-copy {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.sidebar-brand-copy strong {
  font-size: 0.98rem;
  font-weight: 760;
  line-height: 1.2;
}

.sidebar-brand-copy small {
  color: var(--text-secondary);
  font-size: 0.7rem;
}

.sidebar-close {
  display: none;
  margin-left: auto;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 18px 12px;
  overflow-y: auto;
}

/* 返回工作台：整个后台里最需要「清晰回到前台」的入口，放菜单最顶上。 */
.menu-back {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  margin-bottom: 6px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.86rem;
  font-weight: 600;
  text-decoration: none;
  transition:
    color 150ms ease,
    background-color 150ms ease;
}

.menu-back:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

.menu-group-label {
  padding: 16px 12px 7px;
  color: var(--text-muted);
  font-size: 0.68rem;
  font-weight: 720;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.88rem;
  font-weight: 560;
  text-decoration: none;
  transition:
    color 150ms ease,
    background-color 150ms ease;
}

.menu-item:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

/* 激活项：左色条 + 浅青底。router-link-active 由 RouterLink 在命中时自动加上。 */
.menu-item.router-link-active {
  color: var(--accent);
  font-weight: 640;
  background: var(--accent-soft);
  box-shadow: inset 3px 0 var(--accent);
}

.menu-item svg {
  flex: 0 0 auto;
  color: var(--accent);
}

/* 窄屏抽屉遮罩 */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  z-index: 35;
  border: 0;
  background: var(--surface-overlay);
}

.admin-main-wrap {
  flex: 1;
  min-width: 0;
  margin-left: 244px;
}

.admin-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 64px;
  padding: 0 28px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-scrim);
  backdrop-filter: blur(12px);
}

.menu-toggle {
  display: none;
}

.topbar-heading {
  min-width: 0;
}

.topbar-subtitle {
  color: var(--text-secondary);
  font-size: 0.7rem;
  font-weight: 700;
}

.topbar-title {
  margin: 1px 0 0;
  font-size: 1.08rem;
  font-weight: 720;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-actions {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}

.account-identity {
  max-width: 220px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.75rem;
  text-decoration: none;
  transition:
    color 150ms ease,
    background-color 150ms ease;
}

.account-identity:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

.account-identity span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-error {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  color: var(--danger);
  font-size: 0.7rem;
  white-space: nowrap;
}

.admin-content {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 30px 40px 64px;
}

/* 桌面端隐藏汉堡与关闭键；窄屏收起侧边栏为抽屉。 */
@media (min-width: 901px) {
  .menu-toggle {
    display: none;
  }
}

@media (max-width: 900px) {
  .admin-sidebar {
    transform: translateX(-100%);
    box-shadow: var(--shadow-drawer);
    transition: transform 200ms ease;
  }

  .admin-sidebar.is-open {
    transform: translateX(0);
  }

  .menu-toggle {
    display: inline-flex;
  }

  .sidebar-close {
    display: inline-flex;
  }

  .admin-main-wrap {
    margin-left: 0;
  }

  .admin-topbar {
    padding: 0 16px;
  }

  .account-identity {
    display: none;
  }
}

@media (max-width: 640px) {
  .admin-content {
    padding: 22px 16px 56px;
  }

  .admin-topbar {
    gap: 10px;
  }

  .topbar-subtitle {
    display: none;
  }
}
</style>
