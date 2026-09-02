<script setup lang="ts">
import { authSession } from '../features/auth/auth-session'
import PasswordChangeForm from '../features/account/components/PasswordChangeForm.vue'
import { RouterLink } from 'vue-router'
import { User, ShieldCheck } from '@lucide/vue'

const user = authSession.user
</script>

<template>
  <div class="account-page">
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
            <dt class="info-label">创建时间</dt>
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
  </div>
</template>

<style scoped>
.account-page {
  display: grid;
  place-items: center;
  padding: var(--space-6) var(--space-4);
  min-height: 100vh;
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
  color: var(--accent-primary);
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
  color: var(--accent-primary);
  background: var(--surface-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-full);
  text-decoration: none;
  transition: all 0.15s ease;
}

.admin-link:hover {
  background: var(--surface-hover);
  border-color: var(--accent-primary);
}
</style>
