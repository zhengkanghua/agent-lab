<script setup lang="ts">
import { computed, ref } from 'vue'
import { LogIn, RefreshCw, Search } from '@lucide/vue'
import { useRoute, useRouter } from 'vue-router'
import { resolveErrorCopy } from '@/api/error-copy'
import { queryClient } from '@/app/query-client'
import { authSession } from '@/features/auth'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseField from '@/shared/ui/BaseField.vue'
import BaseInput from '@/shared/ui/BaseInput.vue'
import PasswordInput from '@/shared/ui/PasswordInput.vue'

const route = useRoute()
const router = useRouter()
const email = ref(import.meta.env.VITE_DEV_LOGIN_EMAIL || '')
const password = ref(import.meta.env.VITE_DEV_LOGIN_PASSWORD || '')
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
    <main class="login-main">
      <!-- 居中单列（2026-09 重设计 P5）：小品牌锁、28px 主标、一句说明、一张表单卡。
           原左右分栏的装饰图形与英文微大写撤掉了——「精心设计过的简陋感」的来源。 -->
      <section class="login-card" aria-labelledby="login-title">
        <a class="login-brand" href="/" aria-label="Signal Desk 首页">
          <span class="brand-mark" aria-hidden="true">
            <Search :size="19" stroke-width="2.2" />
          </span>
          <span class="brand-copy">
            <strong>Signal Desk</strong>
            <small>知识库语义研究台</small>
          </span>
        </a>

        <h1 id="login-title" class="login-title">进入资料研究台</h1>
        <p class="login-intro">使用平台管理员为你创建的账号继续。</p>

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

        <!-- 两个字段都标 required：提交键在任一为空时是禁用的（见下方 :disabled），
             而「为什么点不动」只有标签上这枚星标能回答。required 经 BaseField 的
             controlAttrs 落到 input 上，不必再在 input 上写一遍。 -->
        <form class="login-form" novalidate @submit.prevent="submitLogin">
          <BaseField v-slot="{ control }" label="账号邮箱" required>
            <BaseInput
              v-bind="control"
              v-model="email"
              name="username"
              type="email"
              autocomplete="username"
              inputmode="email"
              autofocus
              placeholder="name@example.com"
            />
          </BaseField>

          <BaseField v-slot="{ control }" label="密码" required>
            <PasswordInput
              v-bind="control"
              v-model="password"
              name="password"
              autocomplete="current-password"
              placeholder="输入密码"
            />
          </BaseField>

          <BaseCallout
            v-if="loginError"
            class="login-error"
            tone="danger"
            :description="loginError"
          />

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
      </section>
    </main>

    <footer class="login-footnote">受限访问 · 账号由管理员创建</footer>
  </div>
</template>

<style scoped>
.login-shell {
  display: flex;
  flex-direction: column;
  /* 同 style.css 的 body：手机上 100vh 按地址栏收起时的高度算，卡片会被压低、
     底部那行脚注被推出屏幕。两条都写，dvh 不支持时退回上一条。 */
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--surface-base);
}

.login-main {
  display: grid;
  flex: 1 1 auto;
  place-items: center;
  padding: var(--space-8) var(--space-5);
}

/* 表单卡：16 圆角、细描边、轻阴影。输入框皮肤与 44px 高度归全站的 BaseInput。 */
.login-card {
  width: min(100%, 420px);
  padding: var(--space-8) var(--space-8) var(--space-6);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
  box-shadow: var(--shadow-card);
}

/* 品牌锁：brand-mark / brand-copy 见 styles/components/topbar.css（LoginPage 仍是
   这组共享类的合法使用方）。卡片内居中。 */
.login-brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 11px;
  /* 品牌锁同时是回首页的链接，但原来点上去没有任何回应；补一层悬停底。
     负外边距把多出来的内边距还回去，锁在卡片里仍然居中、行高不变。 */
  margin: -6px -10px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.login-brand:hover {
  background: var(--surface-hover);
}

.login-brand strong {
  font-size: var(--fs-base);
  font-weight: var(--fw-bold);
  letter-spacing: 0;
  line-height: 1.2;
}

.login-brand small {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.login-title {
  margin-top: var(--space-6);
  color: var(--text-primary);
  font-family: var(--display-font);
  font-size: var(--fs-3xl);
  font-weight: var(--fw-semibold);
  letter-spacing: 0;
  line-height: var(--lh-heading);
  text-align: center;
}

.login-intro {
  margin-top: 10px;
  color: var(--text-tertiary);
  font-size: var(--fs-sm);
  text-align: center;
}

.login-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: var(--space-5);
  padding: 12px 14px;
  border: 1px solid var(--border-subtle);
  border-left: 3px solid var(--warning);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-base);
  font-size: var(--fs-xs);
}

/* ghost/xs 已经带了内联排布、6px 间距、零内边距与强调色。这里只补一条：
   space-between 的行里默认可压缩，文字会被挤成两行。 */
.notice-retry {
  flex: 0 0 auto;
}

.login-form {
  display: grid;
  gap: var(--space-4);
  margin-top: var(--space-6);
}

/* 标签、错误、说明与 aria 接线都归 BaseField；输入框皮肤归 BaseInput（全站一份）。
   密码框与「显示密码」开关收在 shared/ui/PasswordInput.vue——改密码、建号、重置密码
   三处密码框走同一份，不再各自实现一遍。 */
.login-error {
  margin-bottom: var(--space-2);
}

.login-footnote {
  padding: var(--space-4) var(--space-5) var(--space-5);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  text-align: center;
}

@container (max-width: 480px) {
  .login-main {
    padding: var(--space-6) var(--space-4);
  }

  .login-card {
    padding: var(--space-6) var(--space-5) var(--space-5);
  }
}
</style>
