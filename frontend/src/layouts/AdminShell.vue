<script setup lang="ts">
import { ref } from 'vue'
import { ArrowLeft, CalendarClock, LogOut, Menu, ShieldCheck, UsersRound, X } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import { authSession, useLogout } from '@/features/auth'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import ThemeToggle from '@/shared/ui/ThemeToggle.vue'

/* 后台控制台布局：固定左侧导航 + 右侧内容区。后台只有一条路由（/admin/:section?），
 * 页面（AdminPage）像前台页面嵌 AppShell 一样把本外壳嵌进模板，正文经默认插槽进来。
 *
 * 与 AppShell 的区别是刻意的：AppShell 是前台工作台的单条顶栏，AdminShell 是管理端的
 * 侧边栏布局，二者不互相归并，各自只服务一个领域。
 *
 * 后台导航在这里集中定义（侧边栏对后台所有分区一致），新增后台分区时：
 *   1) 在 pages/AdminPage.vue 的分区注册表加一条（标题/分区说明）；
 *   2) 在下方的 adminMenuItems 加一项。
 * 权限守卫挂在 /admin 路由上，新分区自动继承「后台只给超管」。
 * 退出登录归本外壳持有：顶栏的退出键在这里，退登成功后跳登录页会卸载当前分区，
 * 分区里的敏感输入随之被回收，不必让每个分区各自接线退登。
 */

const props = withDefaults(
  defineProps<{
    /** 顶栏标题与分区说明，由 AdminPage 按当前分区传入（后台只有一条路由，
        不能再从 route.meta 取）。 */
    headingTitle?: string
    headingSubtitle?: string
  }>(),
  { headingTitle: '管理控制台', headingSubtitle: undefined },
)

const adminMenuItems = [
  { to: { name: 'admin', params: { section: 'users' } }, label: '账号管理', icon: UsersRound },
  {
    to: { name: 'admin', params: { section: 'scheduled-jobs' } },
    label: '定时任务',
    icon: CalendarClock,
  },
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
          <p v-if="props.headingSubtitle" class="topbar-subtitle">{{ props.headingSubtitle }}</p>
          <h1 class="topbar-title" :title="props.headingTitle">{{ props.headingTitle }}</h1>
        </div>

        <div class="topbar-actions">
          <ThemeToggle />

          <RouterLink
            v-if="authSession.user.value"
            :to="{ name: 'settings', params: { section: 'account' } }"
            class="account-identity"
            :aria-label="`账号与设置 - ${authSession.user.value.email}`"
            :title="`账号与设置 - ${authSession.user.value.email}`"
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
        <slot />
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
  z-index: var(--z-admin-sidebar);
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
  color: var(--text-tertiary);
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
  z-index: var(--z-admin-overlay);
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
  z-index: var(--z-admin-topbar);
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
    transition: transform var(--duration-normal) var(--ease-out-smooth);
  }

  .admin-sidebar.is-open {
    transform: translateX(0);
  }

  .sidebar-overlay {
    animation: overlayFadeIn var(--duration-normal) var(--ease-out-smooth);
  }

  @keyframes overlayFadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
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
