<script setup lang="ts">
import { computed, type Component } from 'vue'
import { LogOut, UserRound } from '@lucide/vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'
import { authSession } from '@/features/auth'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'
import ThemeToggle from '@/shared/ui/ThemeToggle.vue'

/* 登录后前台三页（检索 / Agent 对话 / 设置）共用的外壳：跳转链接、顶栏。
 *
 * 收编各页自写一份顶栏的差异集中在四处：品牌文案与图标、mode-note 的有无、导航入口、
 * 窄屏断点。前三处做成入口，不为了统一抹平；断点原先开放成 compactAt prop，
 * 旧账号页消失后已无人传第二档，收成一个 560px 常量（消融）。
 *
 * 正文由调用方放进默认插槽，连 <main> 一起——那上面的类是各页自己的骨架
 * （检索页单列流、设置页左导航右内容），scoped 样式必须写在各页里才生效。
 * 外壳只负责提供跳转链接的落点，因此需要 mainId 与页面的 <main id> 对上。
 *
 * 直接读 authSession 而不是让调用方传邮箱：会话是应用级单例，三页都只是显示它。
 * 传进来会让每页多一份 v-if 判空，而那个判断三页完全一样。
 */

interface TopbarNavLink {
  /** 目标路由。图标入口一律走路由，不用原生 a。 */
  to: RouteLocationRaw
  /** 无障碍名，同时作为 title。 */
  label: string
  /** lucide 图标组件本身，不是名字。 */
  icon: Component
  /** 省略即恒显示。用于「仅超管可见」这类条件入口。 */
  visible?: boolean
}

const props = withDefaults(
  defineProps<{
    brandTitle: string
    brandSubtitle: string
    /** 品牌区的无障碍名。文案本身分两行，读屏拼起来读不通顺。 */
    brandLabel: string
    /** 品牌区跳站外/站根用 href，跳本应用路由用 to。二者只给一个。 */
    brandHref?: string
    brandTo?: RouteLocationRaw
    /** 跳转链接的落点，必须与页面 <main> 的 id 一致。 */
    mainId: string
    skipLabel: string
    navLinks?: TopbarNavLink[]
    /** 省略则整个 mode-note 不渲染（设置页没有这一块）。 */
    modeLabel?: string
    modeDetail?: string
    loggingOut?: boolean
    logoutError?: boolean
  }>(),
  {
    brandHref: undefined,
    brandTo: undefined,
    navLinks: () => [],
    modeLabel: undefined,
    modeDetail: undefined,
    loggingOut: false,
    logoutError: false,
  },
)

const emit = defineEmits<{ logout: [] }>()

/* 给 a 传 to、给 RouterLink 传 href 都会落成一个没用的 DOM 属性，所以按分支只给一个。 */
const brandIs = computed(() => (props.brandTo ? RouterLink : 'a'))
const brandAttrs = computed(() =>
  props.brandTo ? { to: props.brandTo } : { href: props.brandHref ?? '/' },
)

const visibleNavLinks = computed(() => props.navLinks.filter((link) => link.visible !== false))
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" :href="`#${mainId}`">{{ skipLabel }}</a>

    <header class="topbar">
      <div class="content-wrap topbar-inner">
        <component :is="brandIs" v-bind="brandAttrs" class="brand-lockup" :aria-label="brandLabel">
          <span class="brand-mark" aria-hidden="true">
            <slot name="brand-icon" />
          </span>
          <span class="brand-copy">
            <strong>{{ brandTitle }}</strong>
            <small>{{ brandSubtitle }}</small>
          </span>
        </component>

        <div class="topbar-actions">
          <div v-if="modeLabel" class="mode-note">
            <span class="mode-dot" aria-hidden="true"></span>
            <span>{{ modeLabel }}</span>
            <span v-if="modeDetail" class="mode-detail">{{ modeDetail }}</span>
          </div>

          <div class="account-control">
            <slot name="nav" />

            <RouterLink
              v-for="link in visibleNavLinks"
              :key="link.label"
              class="topbar-nav-link"
              :to="link.to"
              :aria-label="link.label"
              :title="link.label"
            >
              <component :is="link.icon" :size="17" aria-hidden="true" />
            </RouterLink>

            <ThemeToggle />

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

            <BaseIconButton
              label="退出登录"
              busy-cursor
              :disabled="loggingOut"
              @click="emit('logout')"
            >
              <LogOut :size="17" aria-hidden="true" />
            </BaseIconButton>

            <span v-if="logoutError" class="logout-error" role="alert">退出失败</span>
          </div>
        </div>
      </div>
    </header>

    <slot />
  </div>
</template>

<style scoped>
/* .app-shell、.content-wrap、.skip-link 见 style.css；
   .topbar-inner、.brand-mark、.brand-copy 见 styles/components/topbar.css。 */

/* 顶栏总高对外暴露成自定义属性：Agent 页要让正文区正好占满「视口减顶栏」，
   否则底部贴靠的输入区要么被挤出屏幕、要么留一条空隙。
   68px 来自 .topbar-inner 的 min-height（topbar.css），+1px 是下边框。
   自定义属性沿 DOM 继承，不受 scoped 限制，所以子页面能读到；窄屏断点在下面改写它。 */
.app-shell {
  --app-topbar-height: 69px;
}

.topbar {
  position: sticky;
  z-index: var(--z-topbar);
  top: 0;
  border-bottom: 1px solid var(--border-subtle);
  /* --surface-scrim 本身 96% 不透明，毛玻璃几乎没有可见效果；而 backdrop-filter
     在主题切换（根背景色突变）时会闪出一帧黑色矩形（Chromium 已知伪影，
     2026-09 老板实测检索页输入坞同款问题），不值得为不可见的效果保留。 */
  background: var(--surface-scrim);
}

.brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

.brand-copy strong {
  font-size: 1rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.2;
}

.brand-copy small {
  color: var(--text-secondary);
  font-size: 0.72rem;
  letter-spacing: 0;
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
  border-left: 1px solid var(--border-subtle);
}

.mode-note {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 0.78rem;
  white-space: nowrap;
}

.mode-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 4px var(--accent-soft);
}

.mode-detail {
  padding-left: 8px;
  border-left: 1px solid var(--border-subtle);
  color: var(--text-secondary);
}

/* 与 BaseIconButton 的 md 同尺寸同悬停：这一行里图标入口和退出键要看起来是一排。
   不复用那个组件，因为它渲染的是 button——导航入口是链接，中键点开、复制地址、
   读屏报「链接」都依赖真的 a。 */
.topbar-nav-link {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  text-decoration: none;
  transition:
    border-color var(--duration-fast) var(--ease-out-smooth),
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
}

.topbar-nav-link:hover {
  border-color: var(--border-subtle);
  color: var(--accent);
  background: var(--surface-base);
}

.account-identity {
  max-width: 220px;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 0.75rem;
  text-decoration: none;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth);
  padding: 6px 10px;
  border-radius: var(--radius-sm);
}

.account-identity:hover {
  color: var(--accent);
  background: var(--surface-base);
}

.account-identity span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-identity svg {
  flex: 0 0 auto;
  color: var(--accent);
}

.logout-error {
  position: absolute;
  top: calc(100% + 9px);
  right: 0;
  color: var(--danger);
  font-size: 0.7rem;
  white-space: nowrap;
}

@media (max-width: 560px) {
  .app-shell {
    --app-topbar-height: 63px;
  }

  .topbar-inner {
    min-height: 62px;
  }

  .brand-copy small,
  .mode-detail,
  .account-identity {
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
}
</style>
