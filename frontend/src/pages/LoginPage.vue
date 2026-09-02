<script setup lang="ts">
import { computed, ref } from 'vue'
import { Eye, EyeOff, LogIn, RefreshCw, ScanSearch, Search } from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import { resolveErrorCopy } from '@/api/error-copy'
import { queryClient } from '@/app/query-client'
import { authSession } from '@/features/auth/auth-session'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseIconButton from '@/shared/ui/BaseIconButton.vue'

const route = useRoute()
const router = useRouter()
const email = ref(import.meta.env.VITE_DEV_LOGIN_EMAIL || '')
const password = ref(import.meta.env.VITE_DEV_LOGIN_PASSWORD || '')
const showPassword = ref(false)
const submitting = ref(false)
const loginError = ref<string | null>(null)
const sessionUnavailable = computed(() => authSession.status.value === 'error')

// 登录只有两种要单独说明的失败：凭据不对（后端 400）和连不上（client.ts 在 fetch
// 失败时把 status 记成 0）。两者的 code 分别是 unknown_error 和 network_error，
// 但按状态分类更直观，也不依赖后端是否在 400 响应体里给 code。
const LOGIN_MESSAGE_BY_STATUS: Readonly<Partial<Record<number, string>>> = {
  400: '账号或密码不正确，请重新输入。',
  0: '暂时无法连接登录服务，请检查网络后重试。',
}

function safeRedirect(value: unknown): string {
  if (
    typeof value === 'string' &&
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.startsWith('/login')
  ) {
    return value
  }
  return '/'
}

async function submitLogin(): Promise<void> {
  if (submitting.value) return

  submitting.value = true
  loginError.value = null
  try {
    await authSession.login(email.value.trim(), password.value)
    queryClient.clear()
    await router.replace(safeRedirect(route.query.redirect))
  } catch (cause) {
    loginError.value = resolveErrorCopy(cause, {
      byStatus: LOGIN_MESSAGE_BY_STATUS,
      fallback: '登录没有完成，请稍后重试。',
    })
  } finally {
    submitting.value = false
  }
}

async function retrySessionCheck(): Promise<void> {
  await authSession.initialize(true)
  if (authSession.status.value === 'authenticated') {
    await router.replace(safeRedirect(route.query.redirect))
  }
}
</script>

<template>
  <div class="login-shell" style="container-type: inline-size">
    <header class="login-topbar">
      <div class="login-wrap topbar-inner">
        <a class="login-brand" href="/" aria-label="Signal Desk 首页">
          <span class="brand-mark" aria-hidden="true">
            <Search :size="19" stroke-width="2.2" />
          </span>
          <span class="brand-copy">
            <strong>Signal Desk</strong>
            <small>新闻语义研究台</small>
          </span>
        </a>
        <span class="access-label">受限访问</span>
      </div>
    </header>

    <main class="login-wrap login-main">
      <section class="login-context" aria-labelledby="login-title">
        <p class="login-kicker">内部研究工作台</p>
        <h1 id="login-title">进入新闻研究台</h1>
        <p class="login-intro">使用平台管理员为你创建的账号继续。</p>

        <div class="signal-register" aria-hidden="true">
          <div class="register-heading">
            <ScanSearch :size="27" stroke-width="1.8" />
            <span>SEMANTIC INDEX / ACCESS</span>
          </div>
          <div class="register-lines">
            <span class="register-line register-line-long"></span>
            <span class="register-line register-line-medium"></span>
            <span class="register-line register-line-short"></span>
            <span class="register-line register-line-long"></span>
          </div>
          <span class="register-locator"></span>
        </div>
      </section>

      <section class="login-tool" aria-labelledby="credentials-title">
        <div class="tool-heading">
          <p>账号登录</p>
          <h2 id="credentials-title">验证访问身份</h2>
        </div>

        <div v-if="sessionUnavailable" class="login-notice" role="status">
          <span>登录服务暂时不可用。</span>
          <BaseButton
            class="notice-retry"
            variant="ghost"
            size="xs"
            :loading="authSession.status.value === 'loading'"
            @click="retrySessionCheck"
          >
            <template #icon><RefreshCw :size="15" aria-hidden="true" /></template>
            重新连接
          </BaseButton>
        </div>

        <form class="login-form" novalidate @submit.prevent="submitLogin">
          <BaseField v-slot="{ control }" label="账号邮箱">
            <input
              v-bind="control"
              v-model="email"
              class="login-input"
              name="username"
              type="email"
              autocomplete="username"
              inputmode="email"
              required
              autofocus
              placeholder="name@example.com"
            />
          </BaseField>

          <BaseField v-slot="{ control }" label="密码">
            <span class="password-control">
              <input
                v-bind="control"
                v-model="password"
                class="login-input"
                name="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                required
                placeholder="输入密码"
              />
              <BaseIconButton
                class="password-toggle"
                :label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                <EyeOff v-if="showPassword" :size="18" aria-hidden="true" />
                <Eye v-else :size="18" aria-hidden="true" />
              </BaseIconButton>
            </span>
          </BaseField>

          <p v-if="loginError" class="login-error" role="alert">{{ loginError }}</p>

          <BaseButton
            variant="primary"
            block
            type="submit"
            :loading="submitting"
            :disabled="!email || !password"
          >
            <template #icon><LogIn :size="18" aria-hidden="true" /></template>
            {{ submitting ? '正在登录' : '登录' }}
          </BaseButton>
        </form>

        <p class="account-note">账号由平台管理员创建和管理。</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.login-shell {
  min-height: 100vh;
  background: var(--surface-raised);
}

/* .topbar-inner、.brand-mark、.brand-copy 见 styles/components/topbar.css。
   容器不复用 .content-wrap：登录页刻意收窄到 1080px（正文页是 --content-width 1420px），
   且换行断点是 560px 而非 720px，两者只是形状相似，不是同一个容器。 */
.login-wrap {
  width: min(calc(100% - 48px), 1080px);
  margin: 0 auto;
}

.login-topbar {
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-raised);
}

.login-brand {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

.login-brand strong {
  font-size: 1rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.2;
}

.login-brand small {
  color: var(--text-secondary);
  font-size: 0.72rem;
}

.access-label {
  color: var(--text-muted);
  font-family: var(--mono-font);
  font-size: 0.7rem;
}

.login-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 430px);
  align-items: center;
  min-height: calc(100vh - 69px);
  padding-top: 64px;
  padding-bottom: 72px;
}

.login-context {
  min-width: 0;
  padding-right: 72px;
}

.login-kicker,
.tool-heading p {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 760;
}

.login-context h1 {
  margin-top: 10px;
  color: var(--text-primary);
  font-size: 2.65rem;
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.12;
}

.login-intro {
  margin-top: 17px;
  color: var(--text-secondary);
  font-size: 0.96rem;
}

.signal-register {
  position: relative;
  max-width: 480px;
  height: 190px;
  margin-top: 52px;
  overflow: hidden;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
}

.register-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0 17px;
  color: var(--accent);
}

.register-heading span {
  color: var(--text-muted);
  font-family: var(--mono-font);
  font-size: 0.65rem;
}

.register-lines {
  display: grid;
  gap: 13px;
}

.register-line {
  display: block;
  height: 5px;
  background: var(--surface-sunken);
}

.register-line-long {
  width: 88%;
}

.register-line-medium {
  width: 68%;
}

.register-line-short {
  width: 46%;
}

.register-locator {
  position: absolute;
  top: 59px;
  bottom: 18px;
  left: 34%;
  width: 3px;
  background: var(--accent);
}

.register-locator::after {
  position: absolute;
  top: 35px;
  left: -5px;
  width: 13px;
  height: 13px;
  border: 3px solid var(--surface-raised);
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 1px var(--accent);
  content: '';
}

.login-tool {
  padding: 12px 0 12px 64px;
  border-left: 1px solid var(--border-subtle);
}

.tool-heading h2 {
  margin-top: 7px;
  font-size: 1.55rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.2;
}

.login-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 24px;
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-left: 3px solid var(--warning);
  color: var(--text-secondary);
  background: var(--surface-base);
  font-size: 0.78rem;
}

/* ghost/xs 已经带了内联排布、6px 间距、零内边距与强调色。这里只补一条：
   space-between 的行里默认可压缩，文字会被挤成两行。 */
.notice-retry {
  flex: 0 0 auto;
}

.login-form {
  display: grid;
  gap: 20px;
  margin-top: 31px;
}

/* 标签、错误、说明与 aria 接线都归 BaseField；提交与重连按钮归 BaseButton；
   显示密码归 BaseIconButton。留在本页的只有输入框本身的样式——输入框还没抽成
   基础组件，因为 type / inputmode / autocomplete 这些属性面差别太大。
   .password-toggle 的 34px 与输入框的 42px 是耦合的：上右各内缩 4px，
   4 + 34 + 4 = 42 才能正好嵌在输入框里，所以这里只调位置，不改尺寸。 */
.login-input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  background: var(--surface-raised);
  font-size: 0.84rem;
  font-weight: 450;
  outline: none;
  transition:
    border-color 140ms ease,
    box-shadow 140ms ease;
}

.login-input::placeholder {
  color: var(--text-muted);
}

.login-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.password-control {
  position: relative;
  display: block;
}

.password-control .login-input {
  padding-right: 42px;
}

.password-toggle {
  position: absolute;
  top: 4px;
  right: 4px;
}

.login-error {
  padding: 11px 13px;
  border-left: 3px solid var(--danger);
  color: var(--danger);
  background: var(--danger-soft);
  font-size: 0.8rem;
}

.account-note {
  margin-top: 22px;
  color: var(--text-muted);
  font-size: 0.72rem;
}

@container (max-width: 820px) {
  .login-main {
    grid-template-columns: 1fr;
    align-content: start;
    gap: 44px;
    padding-top: 46px;
  }

  .login-context {
    padding-right: 0;
  }

  .signal-register {
    height: 150px;
    margin-top: 36px;
  }

  .login-tool {
    max-width: 560px;
    padding: 40px 0 0;
    border-top: 1px solid var(--border-subtle);
    border-left: 0;
  }
}

@container (max-width: 560px) {
  .login-wrap {
    width: min(calc(100% - 30px), 1080px);
  }

  .topbar-inner {
    min-height: 62px;
  }

  .login-brand small,
  .access-label {
    display: none;
  }

  .login-main {
    gap: 34px;
    padding-top: 34px;
    padding-bottom: 48px;
  }

  .login-context h1 {
    font-size: 2.1rem;
  }

  .signal-register {
    height: 125px;
    margin-top: 29px;
  }

  .register-heading {
    padding: 15px 0 13px;
  }

  .register-lines {
    gap: 9px;
  }

  .register-locator {
    top: 49px;
    bottom: 12px;
  }

  .login-tool {
    padding-top: 31px;
  }
}
</style>
