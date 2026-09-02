<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Check, Plus } from '@lucide/vue'
import { useRouter } from 'vue-router'
import BaseButton from '@/shared/ui/BaseButton.vue'
import { authSession } from '@/features/auth'
import {
  UserCreateForm,
  UserDirectorySummary,
  UserDirectoryTable,
  useAccountCreateForm,
  useUserDirectory
} from '@/features/user-admin'

/* 账号管理页：作为 /admin 的子路由渲染在 AdminShell 的内容区（RouterView）里。
 * 侧边栏、顶部标题栏、退出登录都由 AdminShell 提供；本页只负责账号管理的正文内容。
 * 页面标题「账号管理」写在路由 meta，AdminShell 据此渲染顶栏标题。 */

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

onMounted(() => {
  void directory.load()
})
</script>

<template>
  <section class="admin-page" aria-labelledby="admin-title" style="container-type: inline-size">
    <div class="page-bar">
      <p class="page-intro">创建平台账号、调整使用权限，并在需要时重置密码或撤销登录会话。</p>
      <BaseButton v-if="!createForm.expanded.value" variant="primary" @click="createForm.open">
        <template #icon><Plus :size="18" aria-hidden="true" /></template>
        创建账号
      </BaseButton>
    </div>

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
  </section>
</template>

<style scoped>
/* 后台外壳（侧边栏、顶栏标题、退出）在 layouts/AdminShell.vue。
   本页只排正文内容。表单、概况、目录各自带样式，见 features/user-admin/components/。 */
.admin-page {
  padding-top: 8px;
}

.page-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 26px;
  border-bottom: 1px solid var(--border-subtle);
}

.page-intro {
  max-width: 640px;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.6;
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

@container (max-width: 640px) {
  .page-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
}
</style>
