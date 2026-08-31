<script setup lang="ts">
import { RefreshCw, UsersRound } from '@lucide/vue'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseSpinner from '@/shared/ui/BaseSpinner.vue'
import type { UserAdminDto } from '@/api/user-admin'
import type { DirectoryLoadState } from '../model/admin-validation'
import UserAccountRow from './UserAccountRow.vue'

/* 账号目录：标题、刷新键、三种非就绪态，以及就绪后的表格。
 *
 * 每一行的控件都在 UserAccountRow 里，这里只负责把事件原样往上转——所有请求都归
 * useUserDirectory，中间这一层不自己发请求，也不自己判断能不能改。
 */

const props = defineProps<{
  users: UserAdminDto[]
  loadState: DirectoryLoadState
  loadError: string
  /** 有请求在途的行 id。 */
  busyUserIds: ReadonlySet<string>
  /** 行 id 到该行上一次失败原因的映射。 */
  rowErrors: Readonly<Record<string, string>>
  currentUserId: string | undefined
  /** 展开了密码重置表单的那一行，没有展开则为 null。 */
  resetUserId: string | null
  resetPassword: string
  resetError: string
}>()

const emit = defineEmits<{
  refresh: []
  'set-active': [user: UserAdminDto, value: boolean]
  'set-superuser': [user: UserAdminDto, value: boolean]
  'open-reset': [user: UserAdminDto]
  'update:resetPassword': [value: string]
  'submit-reset': [user: UserAdminDto]
  'cancel-reset': []
  'revoke-sessions': [user: UserAdminDto]
}>()

/** 只给展开的那一行传字符串，其余传 null——行组件据此判断要不要渲染表单。 */
function resetPasswordFor(user: UserAdminDto): string | null {
  return props.resetUserId === user.id ? props.resetPassword : null
}
</script>

<template>
  <section class="directory" aria-labelledby="directory-title">
    <div class="directory-heading">
      <div>
        <p>账号目录</p>
        <h2 id="directory-title">当前访问成员</h2>
      </div>
      <button
        class="refresh-button"
        type="button"
        :disabled="loadState === 'loading'"
        @click="emit('refresh')"
      >
        <RefreshCw :class="{ spin: loadState === 'loading' }" :size="15" aria-hidden="true" />
        刷新
      </button>
    </div>

    <div v-if="loadState === 'loading'" class="directory-state" role="status">
      <BaseSpinner :size="20" />
      正在读取账号目录
    </div>

    <div
      v-else-if="loadState === 'error'"
      class="directory-state directory-state-error"
      role="alert"
    >
      <span>{{ loadError }}</span>
      <BaseButton variant="ghost" size="xs" @click="emit('refresh')">重新加载</BaseButton>
    </div>

    <div v-else-if="users.length === 0" class="directory-state">
      <UsersRound :size="21" aria-hidden="true" />
      当前还没有可管理账号。
    </div>

    <div v-else class="user-table" role="table" aria-label="平台账号列表">
      <div class="user-table-head" role="row">
        <span role="columnheader">账号</span>
        <span role="columnheader">使用状态</span>
        <span role="columnheader">管理权限</span>
        <span role="columnheader">创建时间</span>
        <span role="columnheader">安全操作</span>
      </div>

      <UserAccountRow
        v-for="user in users"
        :key="user.id"
        :user="user"
        :busy="busyUserIds.has(user.id)"
        :error="rowErrors[user.id] ?? ''"
        :current-user-id="currentUserId"
        :reset-password="resetPasswordFor(user)"
        :reset-error="resetError"
        @set-active="emit('set-active', user, $event)"
        @set-superuser="emit('set-superuser', user, $event)"
        @open-reset="emit('open-reset', user)"
        @update:reset-password="emit('update:resetPassword', $event)"
        @submit-reset="emit('submit-reset', user)"
        @cancel-reset="emit('cancel-reset')"
        @revoke-sessions="emit('revoke-sessions', user)"
      />
    </div>
  </section>
</template>

<style scoped>
.directory {
  margin-top: 36px;
}

.directory-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 15px;
}

.directory-heading p {
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 760;
}

.directory-heading h2 {
  margin-top: 4px;
  font-size: 1.24rem;
  font-weight: 760;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 0;
  color: var(--text-secondary);
  background: transparent;
  font-size: 0.73rem;
  font-weight: 680;
}

.refresh-button:hover:not(:disabled) {
  color: var(--accent);
}

/* 刷新键上那圈转动用共享的 .spin（styles/components/motion.css）：
   转的是 RefreshCw 图标本身，不是另外冒出一个转圈，所以没走 BaseSpinner。 */

.directory-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  gap: 10px;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-size: 0.82rem;
}

.directory-state-error {
  flex-direction: column;
  color: var(--danger);
}

/* 表头与每一行共用这一条列宽定义。声明在表格上、由行组件 var() 取用，
   两处各写一份会在改列宽时错开，而错开只能靠眼睛发现。 */
.user-table {
  --user-row-columns: minmax(235px, 1.7fr) minmax(115px, 0.7fr) minmax(125px, 0.8fr)
    minmax(115px, 0.7fr) minmax(220px, 1.3fr);

  border-top: 1px solid var(--text-primary);
}

.user-table-head {
  display: grid;
  grid-template-columns: var(--user-row-columns);
  gap: 18px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-family: var(--mono-font);
  font-size: 0.65rem;
  text-transform: uppercase;
}

@media (max-width: 1040px) {
  /* 表头撤掉之后列宽不再需要对齐，窄屏的列由行组件自己决定。 */
  .user-table-head {
    display: none;
  }
}
</style>
