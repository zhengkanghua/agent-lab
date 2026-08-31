<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { ArrowLeft, Check, Plus, Search } from '@lucide/vue'
import { useRouter } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { authSession } from '@/features/auth/auth-session'
import { useLogout } from '@/features/auth/useLogout'
import UserCreateForm from '@/features/user-admin/components/UserCreateForm.vue'
import UserDirectorySummary from '@/features/user-admin/components/UserDirectorySummary.vue'
import UserDirectoryTable from '@/features/user-admin/components/UserDirectoryTable.vue'
import { useAccountCreateForm } from '@/features/user-admin/composables/useAccountCreateForm'
import { useUserDirectory } from '@/features/user-admin/composables/useUserDirectory'

const router = useRouter()

const currentUserId = computed(() => authSession.user.value?.id)

const directory = useUserDirectory({
  currentUserId: () => currentUserId.value,
  onSelfDowngraded: async () => {
    /* 管理员把自己停用或降级了。重新取一次会话拿到服务端的真实结论，再按结论落地：
       还认这个身份就退回检索页（这一页已经进不去了），不认就去登录页。
       两个动作都属于路由与会话，所以留在页面，不进 feature。 */
    await authSession.initialize(true)
    await router.replace(
      authSession.status.value === 'authenticated' ? { name: 'search' } : { name: 'login' },
    )
  },
})

const createForm = useAccountCreateForm({
  onCreated: directory.acceptCreatedUser,
  onOpen: directory.clearFeedback,
})

/* afterLogout 而不是 beforeLogout：退登失败意味着会话还在，管理员可能就地重试，
   这时不该把他刚输的密码抹掉。 */
const { loggingOut, logoutError, logout } = useLogout({
  afterLogout: () => {
    createForm.clearSensitiveInput()
    directory.clearSensitiveInput()
  },
})

onMounted(() => {
  void directory.load()
})
</script>

<template>
  <AppShell
    brand-title="Signal Desk"
    brand-subtitle="平台访问控制"
    brand-label="Signal Desk 首页"
    brand-href="/"
    main-id="account-workspace"
    skip-label="跳到账号管理"
    background="raised"
    :compact-at="720"
    :logging-out="loggingOut"
    :logout-error="logoutError"
    @logout="logout"
  >
    <template #brand-icon><Search :size="19" stroke-width="2.2" /></template>

    <!-- 这一页的返回入口带文案，不是图标键，所以走 nav 插槽而不是 navLinks。 -->
    <template #nav>
      <RouterLink class="back-link" :to="{ name: 'search' }">
        <ArrowLeft :size="16" aria-hidden="true" />
        返回检索
      </RouterLink>
    </template>

    <main id="account-workspace" class="content-wrap admin-main">
      <section class="page-heading" aria-labelledby="admin-title">
        <div>
          <p class="page-kicker">访问控制 / 内部账号</p>
          <h1 id="admin-title">账号管理</h1>
          <p>创建平台账号、调整使用权限，并在需要时重置密码或撤销登录会话。</p>
        </div>
        <BaseButton v-if="!createForm.expanded.value" variant="primary" @click="createForm.open">
          <template #icon><Plus :size="18" aria-hidden="true" /></template>
          创建账号
        </BaseButton>
      </section>

      <UserCreateForm
        v-if="createForm.expanded.value"
        v-model:email="createForm.email.value"
        v-model:password="createForm.password.value"
        v-model:superuser="createForm.superuser.value"
        :error="createForm.error.value"
        :submitting="createForm.submitting.value"
        @submit="createForm.submit"
        @close="createForm.close"
      />

      <UserDirectorySummary :stats="directory.stats.value" />

      <p v-if="directory.feedback.value" class="feedback" role="status">
        <Check :size="16" aria-hidden="true" />
        {{ directory.feedback.value }}
      </p>

      <UserDirectoryTable
        v-model:reset-password="directory.resetPassword.value"
        :users="directory.users.value"
        :load-state="directory.loadState.value"
        :load-error="directory.loadError.value"
        :busy-user-ids="directory.busyUserIds.value"
        :row-errors="directory.rowErrors.value"
        :current-user-id="currentUserId"
        :reset-user-id="directory.resetUserId.value"
        :reset-error="directory.resetError.value"
        @refresh="directory.load"
        @set-active="directory.setActive"
        @set-superuser="directory.setSuperuser"
        @open-reset="directory.openPasswordReset"
        @submit-reset="directory.submitPasswordReset"
        @cancel-reset="directory.cancelPasswordReset"
        @revoke-sessions="directory.revokeSessions"
      />
    </main>
  </AppShell>
</template>

<style scoped>
/* 顶栏骨架与页脚在 layouts/AppShell.vue。留在本页的只有「返回检索」——
   它是带文案的链接而不是图标键，两个正文页都没有这个入口，所以不入外壳。
   表单、概况、目录三块各自带样式，见 features/user-admin/components/。 */
.back-link {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 0.76rem;
  font-weight: 680;
  text-decoration: none;
}

.back-link:hover {
  color: var(--accent);
}

.admin-main {
  padding-top: 46px;
  padding-bottom: 72px;
}

.page-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 32px;
  padding-bottom: 33px;
}

.page-kicker {
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 760;
}

.page-heading h1 {
  margin-top: 7px;
  font-size: 2.45rem;
  font-weight: 780;
  line-height: 1.1;
}

.page-heading > div > p:last-child {
  max-width: 620px;
  margin-top: 13px;
  color: var(--text-secondary);
  font-size: 0.91rem;
}

.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  padding: 10px 12px;
  border-left: 3px solid var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
  font-size: 0.77rem;
}

@media (max-width: 720px) {
  /* 外壳在同一断点收窄（compact-at="720"），这里只管返回入口自己：文案压掉、
     图标放大，让它退化成一个图标键。 */
  .back-link {
    padding-right: 10px;
    font-size: 0;
  }

  .back-link svg {
    width: 18px;
    height: 18px;
  }

  .admin-main {
    padding-top: 32px;
    padding-bottom: 52px;
  }

  .page-heading {
    align-items: start;
    flex-direction: column;
    gap: 22px;
  }

  .page-heading h1 {
    font-size: 2.05rem;
  }
}
</style>
