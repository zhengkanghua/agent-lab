<script setup lang="ts">
import { nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import {
  ArrowLeft,
  CalendarClock,
  ClipboardCheck,
  FileText,
  Library,
  LogOut,
  Menu,
  Rss,
  ShieldCheck,
  UserRound,
  UsersRound,
  X,
} from '@lucide/vue'
import { RouterLink } from 'vue-router'
import { authSession, useLogout } from '@/features/auth'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import ThemeToggle from '@/shared/ui/ThemeToggle.vue'

/* 后台控制台布局：固定左侧导航 + 右侧内容区。后台只有一条路由（/admin/:section?），
 * 页面（AdminPage）像前台页面嵌 AppShell 一样把本外壳嵌进模板，正文经默认插槽进来。
 *
 * 与 AppShell 的区别是刻意的：两个都是侧栏外壳，但服务不同领域——AppShell 是前台
 * 工作台（品牌 + 主操作 + 会话列表），AdminShell 是管理控制台（分区菜单 + 分区标题
 * 顶栏），导航项、信息密度与读者都不同，二者不互相归并。
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
  { to: { name: 'admin', params: { section: 'knowledge-bases' } }, label: '知识库', icon: Library },
  { to: { name: 'admin', params: { section: 'files' } }, label: '文件资料', icon: FileText },
  {
    to: { name: 'admin', params: { section: 'documents' } },
    label: '文档审核',
    icon: ClipboardCheck,
  },
  { to: { name: 'admin', params: { section: 'sources' } }, label: '来源管理', icon: Rss },
  {
    to: { name: 'admin', params: { section: 'scheduled-jobs' } },
    label: '任务管理',
    icon: CalendarClock,
  },
]

const { loggingOut, logoutError, logout } = useLogout()

/* 移动端抽屉：桌面常驻，窄屏收起为抽屉。 */
const drawerOpen = ref(false)
const isMobile = ref(window.innerWidth <= 900)
const sidebar = ref<HTMLElement | null>(null)
const menuToggle = ref<InstanceType<typeof BaseIconButton> | null>(null)
const drawerClose = ref<InstanceType<typeof BaseIconButton> | null>(null)
let previousBodyOverflow: string | null = null

function openDrawer(): void {
  if (isMobile.value) drawerOpen.value = true
}
function closeDrawer(): void {
  drawerOpen.value = false
}

function releaseDrawer(): void {
  document.removeEventListener('keydown', handleDrawerKeydown)
  if (previousBodyOverflow !== null) {
    document.body.style.overflow = previousBodyOverflow
    previousBodyOverflow = null
  }
}

function handleDrawerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDrawer()
    return
  }
  if (event.key !== 'Tab') return
  const controls = sidebar.value?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled)')
  const first = controls?.[0]
  const last = controls?.[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

watch(drawerOpen, async (open) => {
  if (open) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleDrawerKeydown)
    await nextTick()
    if (drawerOpen.value) drawerClose.value?.focus()
  } else {
    releaseDrawer()
    await nextTick()
    if (isMobile.value) menuToggle.value?.focus()
  }
})

function updateViewport(): void {
  isMobile.value = window.innerWidth <= 900
  if (!isMobile.value) closeDrawer()
}

onMounted(() => window.addEventListener('resize', updateViewport))
onScopeDispose(() => {
  window.removeEventListener('resize', updateViewport)
  releaseDrawer()
})
</script>

<template>
  <div class="admin-shell">
    <a class="skip-link" href="#admin-content" :inert="drawerOpen ? true : undefined">跳到内容</a>

    <!-- 侧边栏：桌面常驻，窄屏变成抽屉。 -->
    <aside
      id="admin-navigation"
      ref="sidebar"
      class="admin-sidebar"
      :class="{ 'is-open': drawerOpen }"
      :inert="isMobile && !drawerOpen ? true : undefined"
      :aria-hidden="isMobile && !drawerOpen ? true : undefined"
      :role="isMobile ? 'dialog' : undefined"
      :aria-modal="drawerOpen ? true : undefined"
      aria-label="后台导航"
    >
      <div class="sidebar-brand">
        <span class="sidebar-brand-mark" aria-hidden="true">
          <ShieldCheck :size="20" stroke-width="2.2" />
        </span>
        <span class="sidebar-brand-copy">
          <strong>Signal Desk</strong>
          <small>管理控制台</small>
        </span>
        <BaseIconButton
          ref="drawerClose"
          class="sidebar-close"
          label="关闭导航"
          @click="closeDrawer"
        >
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
      tabindex="-1"
      @click="closeDrawer"
    ></button>

    <!-- 右侧内容区 -->
    <div class="admin-main-wrap" :inert="drawerOpen ? true : undefined">
      <header class="admin-topbar">
        <BaseIconButton
          ref="menuToggle"
          class="menu-toggle"
          label="打开导航"
          aria-controls="admin-navigation"
          :aria-expanded="drawerOpen"
          @click="openDrawer"
        >
          <Menu :size="19" aria-hidden="true" />
        </BaseIconButton>

        <div class="topbar-heading">
          <h1 class="topbar-title" :title="props.headingTitle">{{ props.headingTitle }}</h1>
          <p v-if="props.headingSubtitle" class="topbar-subtitle" :title="props.headingSubtitle">
            {{ props.headingSubtitle }}
          </p>
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
            <UserRound :size="17" aria-hidden="true" />
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
  z-index: var(--z-drawer-sidebar);
  display: flex;
  flex-direction: column;
  width: 232px;
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
  border-radius: var(--radius-md);
  color: var(--text-on-inverse);
  background: var(--surface-inverse);
}

.sidebar-brand-copy {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.sidebar-brand-copy strong {
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
  line-height: 1.2;
}

.sidebar-brand-copy small {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
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
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
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
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  letter-spacing: 0;
  text-transform: uppercase;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  text-decoration: none;
  transition:
    color 150ms ease,
    background-color 150ms ease;
}

.menu-item:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

/* 激活项：松绿浅底 + 强调色文字（「当前导航态」的强调配额用在这里）。
   router-link-active 由 RouterLink 在命中时自动加上。图标跟文字同色（currentColor），
   不再单独染绿——每个菜单图标都染绿会让强调色面积超标。 */
.menu-item.router-link-active {
  color: var(--accent);
  font-weight: var(--fw-semibold);
  background: var(--accent-soft);
}

.menu-item svg {
  flex: 0 0 auto;
}

/* 窄屏抽屉遮罩 */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-drawer-overlay);
  border: 0;
  background: var(--surface-overlay);
}

.admin-main-wrap {
  flex: 1;
  min-width: 0;
  margin-left: 232px;
}

.admin-topbar {
  position: sticky;
  top: 0;
  z-index: var(--z-admin-topbar);
  display: flex;
  align-items: center;
  gap: 14px;
  /* 40px 极薄一条（2026-09 重设计 P4）：分区标题 + 说明占一行，操作全收图标。
     后台不再有自己的「页面头」，分区标题就是这一条的正文。 */
  min-height: 40px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-subtle);
  /* 同 AppShell 顶栏：scrim 已 96% 不透明，blur 不可见却会在主题切换时闪黑。 */
  background: var(--surface-scrim);
}

.menu-toggle {
  display: none;
}

.topbar-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.topbar-title {
  margin: 0;
  overflow: hidden;
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-subtitle {
  margin: 0;
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-actions {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-shrink: 0;
}

/* 账号入口：40px 薄条里放不下长邮箱，恒为图标态，邮箱进 aria-label 与 title；
   点它去设置中心的账号分区。 */
.account-identity {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  text-decoration: none;
  transition:
    color 150ms ease,
    background-color 150ms ease;
}

.account-identity svg {
  flex-shrink: 0;
}

.account-identity:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

.account-identity span {
  display: none;
}

.logout-error {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  color: var(--danger);
  font-size: var(--fs-xs);
  white-space: nowrap;
}

.admin-content {
  width: 100%;
  max-width: 1100px;
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

  .topbar-actions {
    gap: 4px;
  }
}
</style>
