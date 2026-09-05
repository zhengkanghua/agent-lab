<script setup lang="ts">
import { Calendar, ShieldCheck, UserRound } from '@lucide/vue'
import type { AuthUserDto } from '@/api/auth'
import PasswordChangeForm from './PasswordChangeForm.vue'

/**
 * 设置中心 · 账号安全分区：关于「我自己」的全部内容——登录信息 + 改自己密码。
 * 从原 /account 页整体迁来；管别人（账号目录）仍在 /admin/users，见 ADR 0011。
 *
 * 用户身份由页面传进来：本 feature 不 import features/auth（feature 之间禁止相互导入），
 * 会话是应用级组合层的事实，页面是唯一合法的组合点。
 */
defineProps<{ user: AuthUserDto | null }>()
</script>

<template>
  <section class="account-section" aria-labelledby="account-heading">
    <h2 id="account-heading" class="section-heading">账号安全</h2>

    <div class="info-card">
      <dl class="info-list">
        <div class="info-item">
          <dt class="info-label">
            <UserRound :size="16" aria-hidden="true" />
            <span>登录邮箱</span>
          </dt>
          <dd class="info-value">{{ user?.email }}</dd>
        </div>

        <div class="info-item">
          <dt class="info-label">
            <ShieldCheck :size="16" aria-hidden="true" />
            <span>账号角色</span>
          </dt>
          <dd class="info-value">
            {{ user?.is_superuser ? '超级用户' : '普通用户' }}
          </dd>
        </div>

        <div class="info-item">
          <dt class="info-label">
            <Calendar :size="16" aria-hidden="true" />
            <span>创建时间</span>
          </dt>
          <dd class="info-value">
            {{ user?.created_at ? new Date(user.created_at).toLocaleString('zh-CN') : '—' }}
          </dd>
        </div>
      </dl>
    </div>

    <PasswordChangeForm />
  </section>
</template>

<style scoped>
.section-heading {
  margin: 0 0 var(--space-5);
  color: var(--text-primary);
  font-size: var(--text-2xl);
  font-weight: 760;
}

.info-card {
  padding: var(--space-5);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
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
  color: var(--text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
}

.info-label svg {
  color: var(--text-tertiary);
}

.info-value {
  margin: 0;
  color: var(--text-primary);
  font-size: var(--text-base);
}

@media (max-width: 560px) {
  .info-item {
    grid-template-columns: 1fr;
    gap: var(--space-2);
  }
}
</style>
