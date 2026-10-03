<script setup lang="ts">
import { computed } from 'vue'
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
  /** 是否连已注销的账号一起显示；默认关。 */
  includeDeleted: boolean
  /** 展开了密码重置表单的那一行，没有展开则为 null。 */
  resetUserId: string | null
  resetPassword: string
  resetError: string
}>()

const emit = defineEmits<{
  refresh: []
  'update:includeDeleted': [value: boolean]
  'set-active': [user: UserAdminDto, value: boolean]
  'open-reset': [user: UserAdminDto]
  'update:resetPassword': [value: string]
  'submit-reset': [user: UserAdminDto]
  'cancel-reset': []
  'revoke-sessions': [user: UserAdminDto]
  'delete-account': [user: UserAdminDto]
}>()

/** 只给展开的那一行传字符串，其余传 null——行组件据此判断要不要渲染表单。 */
function resetPasswordFor(user: UserAdminDto): string | null {
  return props.resetUserId === user.id ? props.resetPassword : null
}

function onIncludeDeletedChange(event: Event): void {
  emit('update:includeDeleted', (event.target as HTMLInputElement).checked)
}

/* 开关打开时列表里到底带进来几条已注销的。开关关着时这个数没有意义（列表里本来就没有）。 */
const deletedCount = computed(() => props.users.filter((user) => user.deleted_at !== null).length)
</script>

<template>
  <section class="directory" aria-labelledby="directory-title" style="container-type: inline-size">
    <div class="directory-heading">
      <div>
        <p>账号目录</p>
        <h2 id="directory-title">当前访问成员</h2>
      </div>
      <div class="directory-tools">
        <!-- 开关是控件、不是过滤器：它只决定列表要不要带上已注销的那一份，
             并把参数送进查询键（那才是它真会重新取数的原因）。 -->
        <label class="include-deleted">
          <input
            type="checkbox"
            :checked="includeDeleted"
            :disabled="loadState === 'loading'"
            data-testid="include-deleted"
            @change="onIncludeDeletedChange"
          />
          <span>显示已注销</span>
        </label>
        <!-- 开关打开后要有回应：已注销的那几行长得和普通行一样，一共带出来几条
             得说出来，否则「勾了没变化」会被当成开关坏了（库里本来就没有已注销账号
             时更是完全看不出来）。 -->
        <span v-if="includeDeleted && loadState === 'ready'" class="deleted-hint" role="status">
          {{ deletedCount === 0 ? '当前没有已注销账号' : `其中已注销 ${deletedCount} 个` }}
        </span>
        <BaseButton
          variant="ghost"
          size="sm"
          :disabled="loadState === 'loading'"
          @click="emit('refresh')"
        >
          <template #icon>
            <RefreshCw :class="{ spin: loadState === 'loading' }" :size="15" aria-hidden="true" />
          </template>
          刷新
        </BaseButton>
      </div>
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

    <TransitionGroup
      v-else
      name="list"
      tag="div"
      class="user-table"
      role="table"
      aria-label="平台账号列表"
    >
      <div key="table-head" class="user-table-head" role="row">
        <span role="columnheader">账号</span>
        <span role="columnheader">使用状态</span>
        <span role="columnheader">管理权限</span>
        <span role="columnheader">创建时间</span>
        <span role="columnheader" class="align-center">安全操作</span>
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
        @open-reset="emit('open-reset', user)"
        @update:reset-password="emit('update:resetPassword', $event)"
        @submit-reset="emit('submit-reset', user)"
        @cancel-reset="emit('cancel-reset')"
        @revoke-sessions="emit('revoke-sessions', user)"
        @delete-account="emit('delete-account', user)"
      />
    </TransitionGroup>
  </section>
</template>

<style scoped>
.directory {
  margin-top: 36px;
}

/* 标题区（.directory-heading 及其 p / h2）归共享层 directory.css：
   与任务目录那份角色相同，原本只是各写各的、字号还漂开了一档。 */
.directory-tools {
  display: flex;
  align-items: center;
  gap: 14px;
}

/* 「显示已注销」：一个普通勾选框，字重与刷新键同档，不给它开关外观——
   它调的是列表口径，不是某一行的状态。 */
.include-deleted {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out-smooth);
}

/* 悬停把字色提到主文字档，禁用时收回「整行可点」的承诺——两条都对齐共享层的
   .check-control（表单里那套复选行），否则同一个控件在表单里和目录工具栏里
   表现不一样：这里划过去毫无回应，加载中整行还是手指指针。 */
.include-deleted:hover:not(:has(input:disabled)) {
  color: var(--text-primary);
}

.include-deleted:has(input:disabled) {
  cursor: not-allowed;
}

.include-deleted input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--accent);
  cursor: pointer;
}

.include-deleted input:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* 触屏下这一行要撑到可点高度。整行虽是 label（点文字也能勾），但 14px 的方框
   加一行 12px 字只有 26px 高，手指按偏一点就落到标签外面、什么也不会发生。
   放大的只有行高与方框，字号字重不动。 */
@media (pointer: coarse) {
  .include-deleted {
    min-height: var(--tap-target);
  }

  .include-deleted input {
    width: 20px;
    height: 20px;
  }
}

/* 开关打开后的回应：比开关本身淡一档，它是结果不是控件。 */
.deleted-hint {
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
}

/* 刷新键走 BaseButton（ghost），三处目录/历史面板同款。刷新键上那圈转动用共享的
   .spin（styles/components/motion.css）：转的是 RefreshCw 图标本身，不是另外冒出
   一个转圈，所以没走 BaseSpinner。 */

/* .directory-state 归共享层：styles/components/directory.css（四个目录页同一套）。 */
/* 表头与每一行共用这一条列宽定义。声明在表格上、由行组件 var() 取用，
   两处各写一份会在改列宽时错开，而错开只能靠眼睛发现。 */
.user-table {
  --user-row-columns: minmax(235px, 1.7fr) minmax(115px, 0.7fr) minmax(125px, 0.8fr)
    minmax(115px, 0.7fr) minmax(220px, 1.3fr);

  border-top: 1px solid var(--border-strong);
}

/* 表头 12/600 三级灰（2026-09 重设计 P4）：不再用等宽大写冒充「表头感」，
   层级靠字重与颜色。 */
.user-table-head {
  display: grid;
  grid-template-columns: var(--user-row-columns);
  gap: 18px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-tertiary);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
}

.align-right {
  text-align: right;
}

.align-center {
  text-align: center;
}

/* 960 是「五列排得下」的下限：五列的最小宽之和 810 + 四个 18px 间距 + 两侧 14px 内边距
   = 910px，留 50px 余量。这个数不能凭手感往上抬——.admin-content 的 max-width 是 1100，
   内容盒最多 1020，阈值一旦超过它，五列那一支就永远到不了（2026-10 实测：原值 1040 时，
   1440 屏上表头消失、「创建时间」折到邮箱下面，看起来像「这张表没有列头」）。 */
@container (max-width: 960px) {
  /* 表头撤掉之后列宽不再需要对齐，窄屏的列由行组件自己决定。 */
  .user-table-head {
    display: none;
  }
}
</style>
