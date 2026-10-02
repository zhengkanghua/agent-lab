<script setup lang="ts">
import { computed } from 'vue'
import { KeyRound, RefreshCw, ShieldCheck, Trash2, UserRound } from '@lucide/vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import BaseSwitch from '@/shared/ui/BaseSwitch.vue'
import type { UserAdminDto } from '@/api/user-admin'
import { formatAccountDate } from '../model/user-account'
import UserPasswordResetForm from './UserPasswordResetForm.vue'

/* 账号目录里的一行。
 *
 * 环境托管超级用户这一行大部分控件是禁用的：它由部署 Secret 托管，改不动。禁用之外还给了
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
  'open-reset': []
  'update:resetPassword': [value: string]
  'submit-reset': []
  'cancel-reset': []
  'revoke-sessions': []
  'delete-account': []
}>()

const managed = computed(() => props.user.is_environment_admin)
const isCurrentUser = computed(() => props.user.id === props.currentUserId)
const resetOpen = computed(() => props.resetPassword !== null)
/* 已注销：终态，数据保留但不能登录。它和环境托管不会同时成立——环境托管账号被库里那条
   约束挡着注销不了。 */
const deregistered = computed(() => props.user.deleted_at !== null)
const deregisteredAt = computed(() =>
  props.user.deleted_at === null ? '' : formatAccountDate(props.user.deleted_at),
)

/* 自己那一行的注销键由 stateControlsVisible 整只拿掉，剩下的禁用条件只有环境托管。 */
const deleteBlocked = computed(() => managed.value)

const deleteTitle = computed(() => {
  if (managed.value) return '请修改部署 Secret 后重启服务'
  return '注销账号：账号不再能登录，记录全部保留'
})

/* 停用/启用与注销两个键在这些行上都不渲染：
   - 自己那一行：两个动作都会让操作者当场失去权限；
   - 已注销那一行：没有可改的状态，也没有可再走一次的注销。
   后端各有规则拦它们（`account_self_protected` / `account_already_deleted`），
   界面不提供只是体验，真正的边界在后端。 */
const stateControlsVisible = computed(() => !isCurrentUser.value && !deregistered.value)
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
        <small
          v-if="managed"
          class="managed-badge"
          title="由部署 Secret 托管，请修改 Secret 后重启服务"
        >
          <ShieldCheck :size="11" aria-hidden="true" />
          环境托管
        </small>
        <small v-else-if="deregistered" class="deregistered-badge">已注销</small>
        <small v-else-if="isCurrentUser">当前账号</small>
        <small v-else>数据库账号</small>
      </span>
    </div>

    <div class="status-cell" role="cell">
      <!-- 已注销的行没有开关：注销是终态，没有「改回去」这个动作，
           给一个只能停在那里的开关只会让人以为能动。 -->
      <template v-if="deregistered">
        <span class="status-chip is-deregistered" role="status">已注销</span>
        <small class="deregistered-at">{{ deregisteredAt }} 注销</small>
      </template>
      <template v-else-if="stateControlsVisible">
        <!-- 开关本体走共享 BaseSwitch；data-testid 透传到真正的 checkbox 上。 -->
        <BaseSwitch
          :checked="user.is_active"
          :disabled="managed || busy"
          :label="`${user.email} 使用状态`"
          :title="managed ? '由部署 Secret 管理' : '允许或停止账号使用'"
          :data-testid="`active-${user.id}`"
          @change="emit('set-active', $event)"
        />
      </template>
      <span
        v-if="!deregistered"
        class="status-chip"
        :class="user.is_active ? 'is-on' : 'is-off'"
        role="status"
      >
        {{ user.is_active ? '启用' : '停用' }}
      </span>
    </div>

    <div class="status-cell" role="cell">
      <!-- 管理权限只剩一个只读标记：超管身份只在建号时决定，之后不能改，所以这里
           不该有可操作的控件（见 docs/adr/0038）。 -->
      <span class="status-chip" :class="user.is_superuser ? 'is-on' : 'is-off'" role="status">
        {{ user.is_superuser ? '超级用户' : '普通用户' }}
      </span>
    </div>

    <div class="created-cell" role="cell">
      <span>{{ formatAccountDate(user.created_at) }}</span>
      <small>{{ user.is_verified ? '已确认' : '待确认' }}</small>
    </div>

    <div class="row-actions" role="cell">
      <!-- 已注销的行只留「撤销会话」：改密码对一个登不进来的账号没有意义，
           注销本身也已经到终态。撤销会话是实际动作（第二遍删 0 行），保留它。 -->
      <button
        v-if="!deregistered"
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
        class="action-danger"
        :disabled="busy"
        :data-testid="`sessions-${user.id}`"
        @click="emit('revoke-sessions')"
      >
        <RefreshCw :size="15" aria-hidden="true" />
        撤销会话
      </button>
      <button
        v-if="stateControlsVisible"
        type="button"
        class="action-danger"
        :disabled="deleteBlocked || busy"
        :title="deleteTitle"
        :data-testid="`delete-${user.id}`"
        @click="emit('delete-account')"
      >
        <Trash2 :size="15" aria-hidden="true" />
        注销账号
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

    <BaseCallout v-if="error" class="row-error" tone="danger" :description="error" />
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
  /* 行高 52（2026-09 重设计 P4 的表格规范）。 */
  min-height: 52px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-raised);
  transition: background-color var(--duration-fast) var(--ease-out-smooth);
}

.user-row:hover {
  background: var(--surface-sunken);
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
  width: 28px;
  height: 28px;
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
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-copy small,
.created-cell small,
.status-cell small {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

/* 环境托管徽章：中性描边 + 小盾牌（2026-09 重设计 P4）。行级的 warning 左条与
   斜纹仍保留——徽章解释身份，行纹标记「整行改不动」。 */
.managed-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  padding: 1px 7px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

/* 已注销徽章用 danger 色：它和「停用」不一样，是回不去的那个终态。 */
.deregistered-badge {
  width: fit-content;
  padding: 1px 7px;
  border: 1px solid var(--danger);
  border-radius: var(--radius-sm);
  color: var(--danger);
  font-size: var(--fs-xs);
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 状态软胶囊的公共三档（底色/字号/圆角与 is-on、is-off）归
   styles/components/chip.css；下面只是这一页专有的「已注销」档。
   开关表达操作，胶囊表达状态——两个说法都在，扫一眼不用猜。 */
.status-chip.is-deregistered {
  color: var(--danger);
  background: var(--danger-soft);
}

.deregistered-at {
  white-space: nowrap;
}

/* 开关外观与它的「显示值以接口确认状态为准」行为都归 shared/ui/BaseSwitch.vue。 */

.created-cell span {
  color: var(--text-secondary);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 7px;
}

/* 行内操作 ghost 小键：透明底、悬停浮色；整行悬停或键盘聚焦时才显现，
   触屏常驻（同 ThreadListItem 删除键的先例）。危险操作单独 danger 色。 */
.row-actions button {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  gap: 6px;
  padding: 0 9px;
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  background: transparent;
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  opacity: 0;
  transition:
    color var(--duration-fast) var(--ease-out-smooth),
    background-color var(--duration-fast) var(--ease-out-smooth),
    opacity var(--duration-fast) var(--ease-out-smooth);
}

.user-row:hover .row-actions button:not(:disabled),
.row-actions button:focus-visible,
.row-actions button:disabled {
  opacity: 1;
}

.row-actions button:hover:not(:disabled) {
  color: var(--accent);
  background: var(--surface-hover);
}

.row-actions button.action-danger:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-soft);
}

.row-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

/* 触屏：常驻之外还要撑到可点高度。32px 是鼠标精度换来的密度，
   手指点不中，只把 opacity 置 1 等于「看得见但点不准」。 */
@media (pointer: coarse) {
  .row-actions button {
    opacity: 1;
    min-height: var(--tap-target);
    padding: 0 12px;
  }
}

/* 重置表单与错误行都占满整行：它们属于这一行，不属于某一列。
   表单内部的排布归 UserPasswordResetForm 自己。 */
.row-reset,
.row-error {
  grid-column: 1 / -1;
}

@container (max-width: 960px) {
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
