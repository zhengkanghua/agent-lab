<script setup lang="ts">
import { computed } from 'vue'
import { KeyRound, RefreshCw, ShieldCheck, UserRound } from '@lucide/vue'
import type { UserAdminDto } from '@/api/user-admin'
import { formatCreatedAt } from '../model/user-account'
import UserPasswordResetForm from './UserPasswordResetForm.vue'

/* 账号目录里的一行。
 *
 * 保底管理员这一行大部分控件是禁用的：它由部署 Secret 托管，改不动。禁用之外还给了
 * 斜纹底与 title 说明——只禁用不解释，管理员会以为是坏了。
 */

const props = defineProps<{
  user: UserAdminDto
  /** 该行有请求在途：所有控件禁用，避免同一行并发两次改动。 */
  busy: boolean
  /** 该行上一次操作的失败原因，空串表示没有。 */
  error: string
  /** 当前登录账号的 id，用于标出「当前账号」。 */
  currentUserId: string | undefined
  /** 展开的密码重置表单属于这一行时给出，否则为 null。 */
  resetPassword: string | null
  resetError: string
}>()

const emit = defineEmits<{
  'set-active': [value: boolean]
  'set-superuser': [value: boolean]
  'open-reset': []
  'update:resetPassword': [value: string]
  'submit-reset': []
  'cancel-reset': []
  'revoke-sessions': []
}>()

const managed = computed(() => props.user.is_environment_admin)
const isCurrentUser = computed(() => props.user.id === props.currentUserId)
const resetOpen = computed(() => props.resetPassword !== null)

/* 两个开关各写一个转发函数，不合成「传事件名进来」的那一个：
   defineEmits 的重载签名把事件名与载荷绑在一起，传进来的联合类型两个重载都不匹配。 */
function onActiveToggle(event: Event): void {
  emit('set-active', checkedOf(event))
}

function onSuperuserToggle(event: Event): void {
  emit('set-superuser', checkedOf(event))
}

function checkedOf(event: Event): boolean {
  return (event.target as HTMLInputElement).checked
}
</script>

<template>
  <article
    class="user-row"
    :class="{ 'environment-row': managed }"
    role="row"
    :data-user-id="user.id"
  >
    <div class="user-identity" role="cell">
      <span class="user-avatar" aria-hidden="true">
        <ShieldCheck v-if="managed" :size="17" />
        <UserRound v-else :size="17" />
      </span>
      <span class="user-copy">
        <strong>{{ user.email }}</strong>
        <small v-if="managed" class="managed-note">由部署 Secret 托管</small>
        <small v-else-if="isCurrentUser">当前账号</small>
        <small v-else>数据库账号</small>
      </span>
    </div>

    <div class="status-cell" role="cell">
      <label
        class="switch-control"
        :class="{ 'switch-disabled': managed }"
        :title="managed ? '由部署 Secret 管理' : '允许或停止账号使用'"
      >
        <input
          type="checkbox"
          :checked="user.is_active"
          :disabled="managed || busy"
          :aria-label="`${user.email} 使用状态`"
          :data-testid="`active-${user.id}`"
          @change="onActiveToggle"
        />
        <span aria-hidden="true"></span>
      </label>
      <small>{{ user.is_active ? '启用' : '停用' }}</small>
    </div>

    <div class="status-cell" role="cell">
      <label
        class="switch-control"
        :class="{ 'switch-disabled': managed }"
        :title="managed ? '保底管理员必须保持超级用户' : '授予账号管理权限'"
      >
        <input
          type="checkbox"
          :checked="user.is_superuser"
          :disabled="managed || busy"
          :aria-label="`${user.email} 超级用户权限`"
          :data-testid="`superuser-${user.id}`"
          @change="onSuperuserToggle"
        />
        <span aria-hidden="true"></span>
      </label>
      <small>{{ user.is_superuser ? '超级用户' : '普通用户' }}</small>
    </div>

    <div class="created-cell" role="cell">
      <span>{{ formatCreatedAt(user.created_at) }}</span>
      <small>{{ user.is_verified ? '已确认' : '待确认' }}</small>
    </div>

    <div class="row-actions" role="cell">
      <button
        type="button"
        :disabled="managed || busy"
        :title="managed ? '请修改部署 Secret 后重启服务' : '重置密码'"
        :data-testid="`reset-${user.id}`"
        @click="emit('open-reset')"
      >
        <KeyRound :size="15" aria-hidden="true" />
        重置密码
      </button>
      <button
        type="button"
        :disabled="busy"
        :data-testid="`sessions-${user.id}`"
        @click="emit('revoke-sessions')"
      >
        <RefreshCw :size="15" aria-hidden="true" />
        撤销会话
      </button>
    </div>

    <UserPasswordResetForm
      v-if="resetOpen"
      class="row-reset"
      :email="user.email"
      :password="resetPassword ?? ''"
      :error="resetError"
      :submitting="busy"
      @update:password="emit('update:resetPassword', $event)"
      @submit="emit('submit-reset')"
      @cancel="emit('cancel-reset')"
    />

    <p v-if="error" class="row-error" role="alert">{{ error }}</p>
  </article>
</template>

<style scoped>
/* 列宽由父表格通过 --user-row-columns 发布：表头必须与每一行严格对齐，
   两处各写一份就会在改列宽时错开。窄屏下表头是 display: none，
   那时的列由本组件自己决定。 */
.user-row {
  position: relative;
  display: grid;
  grid-template-columns: var(--user-row-columns);
  align-items: center;
  gap: 18px;
  min-height: 88px;
  padding: 15px 14px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-raised);
  transition: background-color 200ms ease;
}

.user-row:hover {
  background: var(--surface-base);
}

.environment-row {
  border-left: 3px solid var(--warning);
  /* 斜纹标记「这行由环境托管、改不动」。用 color-mix 兑出低透明度，
     而不是写死 rgba：换主题时描边色跟着走，纹理不会留在浅色。 */
  background-image: repeating-linear-gradient(
    -45deg,
    transparent,
    transparent 8px,
    color-mix(in srgb, var(--border-subtle) 16%, transparent) 8px,
    color-mix(in srgb, var(--border-subtle) 16%, transparent) 9px
  );
}

.user-identity,
.status-cell,
.created-cell,
.row-actions {
  min-width: 0;
}

.user-identity {
  display: flex;
  align-items: center;
  gap: 11px;
}

.user-avatar {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border-subtle);
  border-radius: 50%;
  color: var(--accent);
  background: var(--surface-raised);
}

.environment-row .user-avatar {
  border-color: var(--warning);
  color: var(--warning);
}

.user-copy,
.created-cell {
  display: grid;
  gap: 3px;
}

.user-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 0.8rem;
  font-weight: 710;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-copy small,
.created-cell small,
.status-cell small {
  color: var(--text-tertiary);
  font-size: 0.66rem;
}

.managed-note {
  color: var(--warning);
  font-family: var(--mono-font);
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-control {
  position: relative;
  display: inline-flex;
}

/* 真正的 checkbox 留在 DOM 里、只是看不见：键盘与读屏都还操作它，
   下面那个 <span> 只是它的外观。换成 display: none 会把它从可达性树里摘掉。 */
.switch-control input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.switch-control > span {
  position: relative;
  display: block;
  width: 32px;
  height: 18px;
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  background: var(--surface-sunken);
  transition: background 140ms ease;
}

.switch-control > span::after {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--surface-raised);
  box-shadow: var(--shadow-inset-chip);
  content: '';
  transition: transform 140ms ease;
}

.switch-control input:checked + span {
  border-color: var(--accent);
  background: var(--accent);
}

.switch-control input:checked + span::after {
  transform: translateX(14px);
}

.switch-control input:focus-visible + span {
  outline: 3px solid var(--accent-ring);
  outline-offset: 2px;
}

.switch-disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.created-cell span {
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: 0.68rem;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 7px;
}

.row-actions button {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  gap: 6px;
  padding: 0 9px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: var(--surface-raised);
  font-size: 0.7rem;
  font-weight: 650;
}

.row-actions button:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.row-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

/* 重置表单与错误行都占满整行：它们属于这一行，不属于某一列。
   表单内部的排布归 UserPasswordResetForm 自己。 */
.row-reset,
.row-error {
  grid-column: 1 / -1;
}

.row-error {
  padding: 8px 10px;
  border-left: 3px solid var(--danger);
  color: var(--danger);
  background: var(--danger-soft);
  font-size: 0.73rem;
}

@container (max-width: 1040px) {
  .user-row {
    grid-template-columns: minmax(260px, 1.4fr) repeat(2, minmax(120px, 0.7fr));
  }

  .created-cell {
    padding-left: 45px;
  }

  .row-actions {
    grid-column: 2 / -1;
  }
}

@container (max-width: 720px) {
  .user-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 15px 12px;
    padding: 18px 11px;
  }

  .user-identity {
    grid-column: 1 / -1;
  }

  .created-cell {
    padding-left: 0;
  }

  .row-actions {
    grid-column: 1 / -1;
  }
}

@container (max-width: 430px) {
  .user-row {
    grid-template-columns: 1fr;
  }

  .user-identity,
  .row-actions,
  .row-reset {
    grid-column: 1;
  }

  .row-actions button {
    flex: 1 1 auto;
    justify-content: center;
  }
}
</style>
