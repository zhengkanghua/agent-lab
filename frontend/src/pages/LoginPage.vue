<script setup lang="ts">
import { computed, ref } from 'vue'
import { Eye, EyeOff, LogIn, RefreshCw, ScanSearch, Search } from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { queryClient } from '../app/query-client'
import { authSession } from '../features/auth/auth-session'

const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const loginError = ref<string | null>(null)
const sessionUnavailable = computed(() => authSession.status.value === 'error')

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
    if (cause instanceof ApiError && cause.status === 400) {
      loginError.value = '账号或密码不正确，请重新输入。'
    } else if (cause instanceof ApiError && cause.status === 0) {
      loginError.value = '暂时无法连接登录服务，请检查网络后重试。'
    } else {
      loginError.value = '登录没有完成，请稍后重试。'
    }
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
  <div class="login-shell">
    <header class="login-topbar">
      <div class="login-wrap login-topbar-inner">
        <a class="login-brand" href="/" aria-label="Signal Desk 首页">
          <span class="login-brand-mark" aria-hidden="true">
            <Search :size="19" stroke-width="2.2" />
          </span>
          <span>
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
          <button
            type="button"
            :disabled="authSession.status.value === 'loading'"
            @click="retrySessionCheck"
          >
            <RefreshCw :size="15" aria-hidden="true" />
            重新连接
          </button>
        </div>

        <form class="login-form" novalidate @submit.prevent="submitLogin">
          <label class="login-field">
            <span>账号邮箱</span>
            <input
              v-model="email"
              name="username"
              type="email"
              autocomplete="username"
              inputmode="email"
              required
              autofocus
              placeholder="name@example.com"
            />
          </label>

          <label class="login-field">
            <span>密码</span>
            <span class="password-control">
              <input
                v-model="password"
                name="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                required
                placeholder="输入密码"
              />
              <button
                type="button"
                class="password-toggle"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                :title="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                <EyeOff v-if="showPassword" :size="18" aria-hidden="true" />
                <Eye v-else :size="18" aria-hidden="true" />
              </button>
            </span>
          </label>

          <p v-if="loginError" class="login-error" role="alert">{{ loginError }}</p>

          <button class="login-submit" type="submit" :disabled="submitting || !email || !password">
            <LogIn :size="18" aria-hidden="true" />
            {{ submitting ? '正在登录' : '登录' }}
          </button>
        </form>

        <p class="account-note">账号由平台管理员创建和管理。</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.login-shell {
  min-height: 100vh;
  background: var(--paper-50);
}

.login-wrap {
  width: min(calc(100% - 48px), 1080px);
  margin: 0 auto;
}

.login-topbar {
  border-bottom: 1px solid var(--paper-300);
  background: var(--paper-50);
}

.login-topbar-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 68px;
  gap: 24px;
}

.login-brand {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

.login-brand-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: var(--radius-sm);
  color: var(--paper-50);
  background: var(--ink-950);
  box-shadow: inset 4px 0 var(--signal-500);
}

.login-brand > span:last-child {
  display: grid;
  gap: 1px;
}

.login-brand strong {
  font-family: var(--display-font);
  font-size: 1rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.2;
}

.login-brand small {
  color: var(--ink-700);
  font-size: 0.72rem;
}

.access-label {
  color: var(--ink-500);
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
  color: var(--signal-600);
  font-size: 0.75rem;
  font-weight: 760;
}

.login-context h1 {
  margin-top: 10px;
  color: var(--ink-950);
  font-family: var(--display-font);
  font-size: 2.65rem;
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.12;
}

.login-intro {
  margin-top: 17px;
  color: var(--ink-700);
  font-size: 0.96rem;
}

.signal-register {
  position: relative;
  max-width: 480px;
  height: 190px;
  margin-top: 52px;
  overflow: hidden;
  border-top: 1px solid var(--paper-300);
  border-bottom: 1px solid var(--paper-300);
}

.register-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0 17px;
  color: var(--source-600);
}

.register-heading span {
  color: var(--ink-500);
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
  background: var(--paper-200);
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
  background: var(--signal-500);
}

.register-locator::after {
  position: absolute;
  top: 35px;
  left: -5px;
  width: 13px;
  height: 13px;
  border: 3px solid var(--paper-50);
  border-radius: 50%;
  background: var(--signal-500);
  box-shadow: 0 0 0 1px var(--signal-500);
  content: '';
}

.login-tool {
  padding: 12px 0 12px 64px;
  border-left: 1px solid var(--paper-300);
}

.tool-heading h2 {
  margin-top: 7px;
  font-family: var(--display-font);
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
  border: 1px solid var(--paper-300);
  border-left: 3px solid var(--warning-600);
  color: var(--ink-700);
  background: var(--paper-100);
  font-size: 0.78rem;
}

.login-notice button {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  color: var(--source-600);
  background: transparent;
  font-weight: 700;
}

.login-form {
  display: grid;
  gap: 20px;
  margin-top: 31px;
}

.login-field {
  display: grid;
  gap: 8px;
  color: var(--ink-800);
  font-size: 0.78rem;
  font-weight: 700;
}

.login-field input {
  width: 100%;
  height: 46px;
  padding: 0 13px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  color: var(--ink-950);
  background: var(--paper-50);
  font-size: 0.9rem;
  font-weight: 450;
  outline: none;
  transition:
    border-color 140ms ease,
    box-shadow 140ms ease;
}

.login-field input::placeholder {
  color: var(--ink-500);
}

.login-field input:focus {
  border-color: var(--source-500);
  box-shadow: 0 0 0 3px var(--source-100);
}

.password-control {
  position: relative;
  display: block;
}

.password-control input {
  padding-right: 46px;
}

.password-toggle {
  position: absolute;
  top: 4px;
  right: 4px;
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--ink-500);
  background: transparent;
}

.password-toggle:hover {
  color: var(--ink-950);
  background: var(--paper-100);
}

.login-error {
  padding: 11px 13px;
  border-left: 3px solid var(--danger-600);
  color: var(--danger-600);
  background: var(--danger-100);
  font-size: 0.8rem;
}

.login-submit {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 46px;
  gap: 9px;
  padding: 0 18px;
  border: 1px solid var(--ink-950);
  border-radius: var(--radius-sm);
  color: var(--paper-50);
  background: var(--ink-950);
  font-size: 0.88rem;
  font-weight: 720;
  transition:
    background 140ms ease,
    transform 140ms ease;
}

.login-submit:hover:not(:disabled) {
  background: var(--source-600);
}

.login-submit:active:not(:disabled) {
  transform: translateY(1px);
}

.login-submit:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.account-note {
  margin-top: 22px;
  color: var(--ink-500);
  font-size: 0.72rem;
}

@media (max-width: 820px) {
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
    border-top: 1px solid var(--paper-300);
    border-left: 0;
  }
}

@media (max-width: 560px) {
  .login-wrap {
    width: min(calc(100% - 30px), 1080px);
  }

  .login-topbar-inner {
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
