<script setup lang="ts">
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import {
  Bot,
  LayoutDashboard,
  LogOut,
  Menu,
  Plus,
  RadioTower,
  Search,
  Settings,
  UserRound,
  X,
} from '@lucide/vue'
import { RouterLink } from 'vue-router'
import { authSession } from '@/features/auth'
import { usePreferences } from '@/features/settings'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import ThemeToggle from '@/shared/ui/ThemeToggle.vue'

/* 登录后前台三页（检索 / Agent 对话 / 设置）共用的外壳：左侧栏 + 右侧内容区。
 *
 * 2026-09 重设计（output/design-proposals/frontend-redesign-proposal-2026-09.md）：
 * 顶栏范式换成侧栏范式——ChatGPT / Claude / Gemini 的生产界面已经收敛到同一个
 * 形态：左边一条可导航的侧栏，中间是安静的阅读列，输入区是唯一的主角。
 *
 * 侧栏自上而下四段，职责固定：
 *   1) 品牌区：全站唯一的品牌落点（各页不再各自拼品牌文案），点击回检索首页；
 *   2) 主操作：每页一个（检索=新检索，Agent=新对话），由页面通过 primaryLabel 命名、
 *      监听 primary 事件执行；它是整页允许的最重的一枚按钮；
 *   3) 主导航 + #rail 插槽：导航是应用级事实（检索 / Agent 对话），不属于任何一页，
 *      所以写死在外壳里而不是由页面传入；Agent 页的会话列表经 #rail 插槽进来，
 *      外壳只提供滚动的容器，不管列表内容；
 *   4) 底栏：设置、后台入口（仅超管）、主题切换、账号与退出。后台入口是整个前台
 *      唯一的一处，漏掉就等于那个页面上的用户找不到后台。
 *
 * 当前页由 active 显式标出（aria-current），不靠 router-link-active：
 * /agent/:threadId 与 /agent 是两条路由名，路由态判断会把「在某个会话里」漏掉。
 *
 * 窄屏（≤900px）侧栏收起为抽屉：汉堡键打开，遮罩 / Esc 关闭，焦点在抽屉内循环，
 * 打开时背景不可聚焦、页面锁定滚动。模式与 AdminShell 的抽屉一致。
 *
 * --app-header-offset 继续由本外壳发布（结构守护测试钉在这里）：桌面端没有顶栏，
 * 值为 0px；窄屏有一条 56px 的汉堡条，值为 57px（含 1px 下边框）。页面用它算
 * 「视口减 bar」的高度与 sticky 吸附位置。名字说「偏移」不说「高度」：对页面而言
 * 它的语义是「内容区顶部要让出多少」（2026-09 重设计随顶栏退场由
 * --app-topbar-height 改名，多数页面下值为 0）。
 *
 * 直接读 authSession 而不是让调用方传邮箱：会话是应用级单例，三页都只是显示它。
 * 退登逻辑仍在页面（Agent 页要先掐流、设置页要先确认草稿），外壳只发 logout 事件。
 */

withDefaults(
  defineProps<{
    /** 当前页：决定主导航与设置入口哪个亮。 */
    active: 'search' | 'agent' | 'settings'
    /** 跳转链接的落点，必须与页面 <main> 的 id 一致。 */
    mainId: string
    skipLabel: string
    /** 主操作按钮的文案（如「新检索」「新对话」）。省略则不渲染主操作。 */
    primaryLabel?: string
    loggingOut?: boolean
    logoutError?: boolean
  }>(),
  { primaryLabel: undefined, loggingOut: false, logoutError: false },
)

const emit = defineEmits<{ primary: []; logout: [] }>()

/* 主导航对所有登录账号常驻：检索与 Agent 对话都是登录即可用的能力。
   后台入口仍仅超管可见——权限只是体验层，真正的门在路由 meta.requiresSuperuser
   与后端 current_superuser 上，这里不渲染只是不让普通账号看到点进去必然失败的入口。 */
const isSuperuser = computed(() => authSession.user.value?.is_superuser === true)

const navItems = computed(() => [
  { key: 'search', to: { name: 'search' }, label: '语义检索', icon: Search },
  { key: 'agent', to: { name: 'agent-chat' }, label: 'Agent 对话', icon: Bot },
])

/* 账号偏好在这里读一次，供三个页面共用。
 *
 * 为什么放在外壳而不是各页自己读：检索页提交时要读数量参数、对话页要读提示词判断要不要亮
 * 徽章、设置页要展示与编辑——每一页都读一次就是每次页面切换多一次往返，而且它们读到的
 * 必须是同一份。外壳是三个页面唯一的共同祖先，且它在登录后才渲染。store 内部按账号去重，
 * 所以这里每次挂载都调也不会重复请求。 */
const { load: loadPreferences } = usePreferences()
onMounted(() => {
  void loadPreferences(authSession.user.value?.id)
})

/* 窄屏抽屉：桌面常驻，窄屏收起为抽屉。逻辑与 AdminShell 同款。 */
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

function handlePrimary(): void {
  emit('primary')
  closeDrawer()
}
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" :href="`#${mainId}`" :inert="drawerOpen ? true : undefined">
      {{ skipLabel }}
    </a>

    <!-- 侧栏：桌面常驻，窄屏变成抽屉。 -->
    <aside
      id="app-navigation"
      ref="sidebar"
      class="shell-sidebar"
      :class="{ 'is-open': drawerOpen }"
      :inert="isMobile && !drawerOpen ? true : undefined"
      :aria-hidden="isMobile && !drawerOpen ? true : undefined"
      :role="isMobile ? 'dialog' : undefined"
      :aria-modal="drawerOpen ? true : undefined"
      aria-label="工作台导航"
    >
      <div class="sidebar-brand">
        <RouterLink
          class="brand-lockup"
          :to="{ name: 'search' }"
          aria-label="Signal Desk 首页"
          @click="closeDrawer"
        >
          <span class="shell-brand-mark" aria-hidden="true">
            <RadioTower :size="18" stroke-width="2.2" />
          </span>
          <span class="brand-text">
            <strong>Signal Desk</strong>
            <small>知识库工作台</small>
          </span>
        </RouterLink>
        <BaseIconButton
          ref="drawerClose"
          class="sidebar-close"
          label="关闭导航"
          @click="closeDrawer"
        >
          <X :size="18" aria-hidden="true" />
        </BaseIconButton>
      </div>

      <div v-if="primaryLabel" class="sidebar-primary">
        <BaseButton variant="primary" class="primary-button" @click="handlePrimary">
          <template #icon><Plus :size="16" stroke-width="2.4" aria-hidden="true" /></template>
          {{ primaryLabel }}
        </BaseButton>
      </div>

      <nav class="sidebar-nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.key"
          class="nav-item"
          :class="{ 'is-active': active === item.key }"
          :to="item.to"
          :aria-current="active === item.key ? 'page' : undefined"
          @click="closeDrawer"
        >
          <component :is="item.icon" :size="17" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <!-- Agent 页的会话列表从这里进来；外壳只给滚动容器。 -->
      <div v-if="$slots.rail" class="sidebar-rail">
        <slot name="rail" />
      </div>

      <div class="sidebar-footer">
        <RouterLink
          class="nav-item"
          :class="{ 'is-active': active === 'settings' }"
          :to="{ name: 'settings' }"
          :aria-current="active === 'settings' ? 'page' : undefined"
          @click="closeDrawer"
        >
          <Settings :size="17" aria-hidden="true" />
          <span>设置</span>
        </RouterLink>
        <RouterLink
          v-if="isSuperuser"
          class="nav-item"
          :to="{ name: 'admin' }"
          aria-label="后台管理"
          title="后台管理"
          @click="closeDrawer"
        >
          <LayoutDashboard :size="17" aria-hidden="true" />
          <span>后台管理</span>
        </RouterLink>

        <div class="footer-account">
          <RouterLink
            v-if="authSession.user.value"
            :to="{ name: 'settings', params: { section: 'account' } }"
            class="account-identity"
            :aria-label="`账号与设置 - ${authSession.user.value.email}`"
            :title="`账号与设置 - ${authSession.user.value.email}`"
          >
            <UserRound :size="16" aria-hidden="true" />
            <span>{{ authSession.user.value.email }}</span>
          </RouterLink>
          <ThemeToggle />
          <BaseIconButton
            label="退出登录"
            busy-cursor
            :disabled="loggingOut"
            @click="emit('logout')"
          >
            <LogOut :size="17" aria-hidden="true" />
          </BaseIconButton>
        </div>
        <p v-if="logoutError" class="logout-error" role="alert">退出失败</p>
      </div>
    </aside>

    <!-- 窄屏抽屉遮罩 -->
    <button
      v-if="drawerOpen"
      class="sidebar-overlay"
      aria-label="关闭导航"
      tabindex="-1"
      @click="closeDrawer"
    ></button>

    <!-- 右侧内容区：窄屏多一条汉堡条，正文由调用方放进默认插槽（连 <main> 一起——
         上面的类是各页自己的骨架，scoped 样式必须写在各页里才生效）。 -->
    <div class="shell-body" :inert="drawerOpen ? true : undefined">
      <header class="mobile-bar">
        <BaseIconButton
          ref="menuToggle"
          label="打开导航"
          aria-controls="app-navigation"
          :aria-expanded="drawerOpen"
          @click="openDrawer"
        >
          <Menu :size="19" aria-hidden="true" />
        </BaseIconButton>
        <span class="mobile-brand">Signal Desk</span>
      </header>

      <slot />
    </div>
  </div>
</template>

<style scoped>
/* .app-shell、.skip-link 见 style.css。 */

/* 头部偏移对外暴露：桌面端为 0px，窄屏的汉堡条在断点里改写成 57px。
   自定义属性沿 DOM 继承，不受 scoped 限制，所以子页面能读到。 */
.app-shell {
  --app-header-offset: 0px;
}

/* 侧栏固定贴左、全高；内容区用 margin-left 让位。 */
.shell-sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: var(--z-drawer-sidebar);
  display: flex;
  flex-direction: column;
  width: 264px;
  background: var(--surface-sunken);
  border-right: 1px solid var(--border-subtle);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 16px 10px;
}

.brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  flex: 1;
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
}

/* 类名带 shell- 前缀：topbar.css 的共享层还占着 .brand-mark/.brand-copy（登录页在用），
   结构守护禁止组件重声明共享类。 */
.shell-brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  border-radius: var(--radius-md);
  color: var(--text-on-inverse);
  background: var(--surface-inverse);
}

.brand-text {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.brand-text strong {
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
  line-height: 1.2;
}

.brand-text small {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.sidebar-close {
  display: none;
}

/* 主操作：整页唯一一枚填充强调色的大按钮（强调色纪律里「主按钮」那一处）。 */
.sidebar-primary {
  padding: 4px 16px 12px;
}

.primary-button {
  width: 100%;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 12px 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  text-decoration: none;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.nav-item:hover {
  color: var(--text-primary);
  background: var(--surface-hover);
}

/* 当前页：强调色纪律里「当前导航态」那一处。 */
.nav-item.is-active {
  color: var(--accent);
  background: var(--accent-soft);
}

.nav-item svg {
  flex: 0 0 auto;
}

/* 会话列表区：吃掉侧栏剩余高度并自己滚动——侧栏不再依赖页面滚动来「跟随」。 */
.sidebar-rail {
  flex: 1 1 auto;
  min-height: 0;
  margin-top: 12px;
  padding: 10px 6px 10px 12px;
  overflow-y: auto;
  border-top: 1px solid var(--border-subtle);
}

.sidebar-footer {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px 14px;
  border-top: 1px solid var(--border-subtle);
}

.footer-account {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
}

.account-identity {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex: 1;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  text-decoration: none;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.account-identity:hover {
  color: var(--accent);
  background: var(--surface-hover);
}

.account-identity span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-error {
  padding: 4px 8px 0;
  color: var(--danger);
  font-size: var(--fs-xs);
}

/* 窄屏抽屉遮罩 */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-drawer-overlay);
  border: 0;
  background: var(--surface-overlay);
}

.shell-body {
  flex: 1;
  min-width: 0;
  margin-left: 264px;
}

.mobile-bar {
  display: none;
}

@media (max-width: 900px) {
  /* 56px 汉堡条 + 1px 下边框。 */
  .app-shell {
    --app-header-offset: 57px;
  }

  .shell-sidebar {
    transform: translateX(-100%);
    box-shadow: var(--shadow-drawer);
    transition: transform var(--duration-normal) var(--ease-out-smooth);
  }

  .shell-sidebar.is-open {
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

  .sidebar-close {
    display: inline-flex;
  }

  .shell-body {
    margin-left: 0;
  }

  .mobile-bar {
    position: sticky;
    top: 0;
    z-index: var(--z-topbar);
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 56px;
    padding: 0 12px;
    border-bottom: 1px solid var(--border-subtle);
    /* 同旧顶栏：scrim 已 96% 不透明，不配 backdrop-filter（主题切换会闪黑）。 */
    background: var(--surface-scrim);
  }

  .mobile-brand {
    font-size: var(--fs-sm);
    font-weight: var(--fw-bold);
  }
}

@media (prefers-reduced-motion: reduce) {
  .shell-sidebar {
    transition: none;
  }
}
</style>
