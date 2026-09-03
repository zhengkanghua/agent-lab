<script setup lang="ts">
import { authSession, useLogout } from '@/features/auth'
import { PasswordChangeForm } from '@/features/account'
import { RouterLink } from 'vue-router'
import { User, ShieldCheck, Calendar } from '@lucide/vue'
import AppShell from '@/layouts/AppShell.vue'

const user = authSession.user
const { loggingOut, logoutError, logout } = useLogout()
</script>

<template>
  <!-- 顶栏与其他页一致：品牌块链回检索页，账号页不再是一座信息孤岛。 -->
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="新闻语义研究台"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="account-page"
    skip-label="跳到账号设置"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><User :size="19" stroke-width="2.2" /></template>

    <main id="account-page" class="account-page">
      <div class="account-container">
        <header class="page-header">
          <h1 class="page-title">账号设置</h1>
        </header>

        <section class="account-info">
          <h2 class="section-title">账号信息</h2>
          <dl class="info-list">
            <div class="info-item">
              <dt class="info-label">
                <User :size="16" />
                <span>登录邮箱</span>
              </dt>
              <dd class="info-value">{{ user?.email }}</dd>
            </div>

            <div class="info-item">
              <dt class="info-label">
                <ShieldCheck :size="16" />
                <span>账号角色</span>
              </dt>
              <dd class="info-value">
                {{ user?.is_superuser ? '超级用户' : '普通用户' }}
              </dd>
            </div>

            <div class="info-item">
              <dt class="info-label">
                <Calendar :size="16" />
                <span>创建时间</span>
              </dt>
              <dd class="info-value">
                {{ user?.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : '—' }}
              </dd>
            </div>
          </dl>
        </section>

        <section class="password-section">
          <PasswordChangeForm />
        </section>

        <section v-if="user?.is_superuser" class="admin-section">
          <div class="admin-card">
            <div class="admin-header">
              <ShieldCheck :size="20" />
              <h3 class="admin-title">管理员功能</h3>
            </div>
            <p class="admin-description">您拥有超级用户权限,可以管理系统内的所有账号。</p>
            <RouterLink :to="{ name: 'user-admin' }" class="admin-link"> 前往账号管理 </RouterLink>
          </div>
        </section>
      </div>
    </main>
  </AppShell>
</template>

<style scoped>
/* 高度扣掉顶栏：AppShell 顶栏常驻后，整页 100vh 会多出一条页内滚动。 */
.account-page {
  display: grid;
  place-items: center;
  padding: var(--space-6) var(--space-4);
  min-height: calc(100vh - var(--app-topbar-height, 69px));
  min-height: calc(100dvh - var(--app-topbar-height, 69px));
}

.account-container {
  width: 100%;
  max-width: 48rem;
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
}

.page-header {
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--border-subtle);
}

.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.section-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 var(--space-4) 0;
}

.account-info {
  display: flex;
  flex-direction: column;
}

.info-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin: 0;
}

.info-item {
  display: grid;
  grid-template-columns: 10rem 1fr;
  gap: var(--space-4);
  align-items: baseline;
}

.info-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-secondary);
}

.info-value {
  font-size: var(--text-base);
  color: var(--text-primary);
  margin: 0;
}

.password-section {
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
}

.admin-section {
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
}

.admin-card {
  padding: var(--space-5);
  background: var(--surface-secondary);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.admin-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--accent);
}

.admin-title {
  font-size: var(--text-base);
  font-weight: 600;
  margin: 0;
}

.admin-description {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin: 0;
}

.admin-link {
  align-self: flex-start;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--accent);
  background: var(--surface-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-pill);
  text-decoration: none;
  transition: all 0.15s ease;
}

.admin-link:hover {
  background: var(--surface-hover);
  border-color: var(--accent);
}
</style>
