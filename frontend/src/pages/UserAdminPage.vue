<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArrowLeft,
  Check,
  KeyRound,
  LoaderCircle,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserRound,
  UsersRound,
  X,
} from '@lucide/vue'
import { useRouter } from 'vue-router'
import { resolveErrorCopy } from '../api/error-copy'
import {
  createUser,
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
  type UserAdminDto,
} from '../api/user-admin'
import { queryClient } from '../app/query-client'
import { authSession } from '../features/auth/auth-session'

type LoadState = 'loading' | 'ready' | 'error'

// 与后端 UserAdminCreateRequest / UserAdminPasswordRequest 的 Field 约束一致，用于在提交前
// 给出即时提示。密码策略的其余部分（例如不得与邮箱相同）只由后端判定，前端读 invalid_password。
const PASSWORD_MIN_LENGTH = 12
const PASSWORD_MAX_LENGTH = 128

// 提到模块作用域：Intl.DateTimeFormat 的构造开销远高于 format()，账号列表每行都会调用。
const createdAtFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const router = useRouter()
const users = ref<UserAdminDto[]>([])
const loadState = ref<LoadState>('loading')
const loadError = ref('')
const feedback = ref('')
const loggingOut = ref(false)
const logoutError = ref(false)
const createExpanded = ref(false)
const creating = ref(false)
const createEmail = ref('')
const createPassword = ref('')
const createSuperuser = ref(false)
const createError = ref('')
const busyUserIds = ref(new Set<string>())
const rowErrors = ref<Record<string, string>>({})
const resetUserId = ref<string | null>(null)
const resetPassword = ref('')
const resetError = ref('')
let loadController: AbortController | null = null

const totalUsers = computed(() => users.value.length)
const activeUsers = computed(() => users.value.filter((user) => user.is_active).length)
const superusers = computed(() => users.value.filter((user) => user.is_superuser).length)

onMounted(() => {
  void loadAccounts()
})

onBeforeUnmount(() => {
  loadController?.abort()
  clearSensitiveInputs()
})

async function loadAccounts(): Promise<void> {
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  loadState.value = 'loading'
  loadError.value = ''
  try {
    const loadedUsers = await listUsers(controller.signal)
    if (controller !== loadController) return
    users.value = sortUsers(loadedUsers)
    loadState.value = 'ready'
  } catch (cause) {
    if (controller.signal.aborted || controller !== loadController) return
    loadError.value = adminErrorMessage(cause, '暂时无法读取账号列表，请稍后重试。')
    loadState.value = 'error'
  }
}

function openCreate(): void {
  createExpanded.value = true
  createError.value = ''
  feedback.value = ''
}

function closeCreate(): void {
  if (creating.value) return
  resetCreateForm()
}

function resetCreateForm(): void {
  createExpanded.value = false
  createError.value = ''
  createEmail.value = ''
  createPassword.value = ''
  createSuperuser.value = false
}

async function submitCreate(): Promise<void> {
  if (creating.value) return

  const email = createEmail.value.trim()
  const validation = validateCredentials(email, createPassword.value)
  if (validation) {
    createError.value = validation
    return
  }

  creating.value = true
  createError.value = ''
  feedback.value = ''
  try {
    const created = await createUser({
      email,
      password: createPassword.value,
      isSuperuser: createSuperuser.value,
    })
    users.value = sortUsers([...users.value, created])
    // 不走 closeCreate：那里的 creating 守卫此刻恒为真，会把表单留在展开状态。
    resetCreateForm()
    feedback.value = `已创建账号 ${created.email}。`
  } catch (cause) {
    createError.value = adminErrorMessage(cause, '账号创建失败，请稍后重试。')
  } finally {
    creating.value = false
  }
}

async function changeActive(user: UserAdminDto, event: Event): Promise<void> {
  await updateAccount(user, { isActive: (event.target as HTMLInputElement).checked })
}

async function changeSuperuser(user: UserAdminDto, event: Event): Promise<void> {
  await updateAccount(user, { isSuperuser: (event.target as HTMLInputElement).checked })
}

async function updateAccount(
  user: UserAdminDto,
  change: { isActive?: boolean; isSuperuser?: boolean },
): Promise<void> {
  if (user.is_environment_admin || isBusy(user.id)) return

  setBusy(user.id, true)
  setRowError(user.id, '')
  feedback.value = ''
  try {
    const updated = await updateUser({ userId: user.id, ...change })
    replaceUser(updated)
    feedback.value = `已更新账号 ${updated.email}。`

    if (
      updated.id === authSession.user.value?.id &&
      (!updated.is_active || !updated.is_superuser)
    ) {
      await authSession.initialize(true)
      await router.replace(
        authSession.status.value === 'authenticated' ? { name: 'search' } : { name: 'login' },
      )
    }
  } catch (cause) {
    setRowError(user.id, adminErrorMessage(cause, '账号状态更新失败，请稍后重试。'))
  } finally {
    setBusy(user.id, false)
  }
}

function openPasswordReset(user: UserAdminDto): void {
  if (user.is_environment_admin || isBusy(user.id)) return
  resetUserId.value = resetUserId.value === user.id ? null : user.id
  resetPassword.value = ''
  resetError.value = ''
  setRowError(user.id, '')
}

function cancelPasswordReset(): void {
  resetUserId.value = null
  resetPassword.value = ''
  resetError.value = ''
}

async function submitPasswordReset(user: UserAdminDto): Promise<void> {
  if (isBusy(user.id)) return
  const validation = validatePassword(resetPassword.value)
  if (validation) {
    resetError.value = validation
    return
  }

  setBusy(user.id, true)
  resetError.value = ''
  feedback.value = ''
  try {
    replaceUser(await resetUserPassword({ userId: user.id, password: resetPassword.value }))
    cancelPasswordReset()
    feedback.value = `已重置 ${user.email} 的密码，并撤销该账号的全部会话。`
  } catch (cause) {
    resetError.value = adminErrorMessage(cause, '密码重置失败，请稍后重试。')
  } finally {
    setBusy(user.id, false)
  }
}

async function revokeSessions(user: UserAdminDto): Promise<void> {
  if (isBusy(user.id)) return
  if (!window.confirm(`撤销 ${user.email} 的全部登录会话？`)) return

  setBusy(user.id, true)
  setRowError(user.id, '')
  feedback.value = ''
  try {
    const result = await revokeUserSessions(user.id)
    feedback.value =
      result.revoked_sessions === 0
        ? `${user.email} 当前没有有效会话。`
        : `已撤销 ${user.email} 的 ${result.revoked_sessions} 个会话。`
  } catch (cause) {
    setRowError(user.id, adminErrorMessage(cause, '会话撤销失败，请稍后重试。'))
  } finally {
    setBusy(user.id, false)
  }
}

async function logout(): Promise<void> {
  if (loggingOut.value) return
  loggingOut.value = true
  logoutError.value = false
  try {
    await authSession.logout()
    clearSensitiveInputs()
    queryClient.clear()
    await router.replace({ name: 'login' })
  } catch {
    logoutError.value = true
  } finally {
    loggingOut.value = false
  }
}

function validateCredentials(email: string, password: string): string {
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return '请输入有效的账号邮箱。'
  return validatePassword(password)
}

function validatePassword(password: string): string {
  if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    return `密码长度需要在 ${PASSWORD_MIN_LENGTH} 到 ${PASSWORD_MAX_LENGTH} 个字符之间。`
  }
  return ''
}

// 账号管理的失败都由后端 code 区分，与状态码无关（invalid_password 是 422、
// last_superuser_protected 是 409，但两者要说的话完全不同）。兜底文案按调用点传入，
// 因为「读列表失败」和「改密码失败」该说的下一步动作不一样。
const ADMIN_MESSAGE_BY_CODE: Readonly<Partial<Record<string, string>>> = {
  user_already_exists: '该邮箱已经存在账号。',
  invalid_password: '密码不符合安全要求：需要 12 到 128 个字符，且不能与账号邮箱相同。',
  environment_admin_protected: '保底管理员由部署 Secret 托管，不能在网页中修改。',
  last_superuser_protected: '不能停用或降级最后一个启用的超级用户。',
  user_not_found: '该账号已不存在，请刷新列表。',
  permission_denied: '当前账号没有管理权限。',
  invalid_request: '提交内容不符合账号管理要求，请检查后重试。',
}

function adminErrorMessage(cause: unknown, fallback: string): string {
  return resolveErrorCopy(cause, { byCode: ADMIN_MESSAGE_BY_CODE, fallback })
}

function sortUsers(items: UserAdminDto[]): UserAdminDto[] {
  return [...items].sort((left, right) => {
    if (left.is_environment_admin !== right.is_environment_admin) {
      return left.is_environment_admin ? -1 : 1
    }
    return left.email.localeCompare(right.email)
  })
}

function replaceUser(updated: UserAdminDto): void {
  users.value = sortUsers(users.value.map((user) => (user.id === updated.id ? updated : user)))
}

function isBusy(userId: string): boolean {
  return busyUserIds.value.has(userId)
}

function setBusy(userId: string, busy: boolean): void {
  const next = new Set(busyUserIds.value)
  if (busy) next.add(userId)
  else next.delete(userId)
  busyUserIds.value = next
}

function setRowError(userId: string, message: string): void {
  rowErrors.value = { ...rowErrors.value, [userId]: message }
}

function formatCreatedAt(value: string): string {
  return createdAtFormatter.format(new Date(value))
}

function clearSensitiveInputs(): void {
  createPassword.value = ''
  resetPassword.value = ''
}
</script>

<template>
  <div class="admin-shell">
    <a class="skip-link" href="#account-workspace">跳到账号管理</a>

    <header class="admin-topbar">
      <div class="content-wrap topbar-inner">
        <a class="brand-lockup" href="/" aria-label="Signal Desk 首页">
          <span class="brand-mark" aria-hidden="true">
            <Search :size="19" stroke-width="2.2" />
          </span>
          <span class="brand-copy">
            <strong>Signal Desk</strong>
            <small>平台访问控制</small>
          </span>
        </a>

        <div class="topbar-actions">
          <RouterLink class="back-link" :to="{ name: 'search' }">
            <ArrowLeft :size="16" aria-hidden="true" />
            返回检索
          </RouterLink>
          <span v-if="authSession.user.value" class="account-identity">
            <UserRound :size="16" aria-hidden="true" />
            <span>{{ authSession.user.value.email }}</span>
          </span>
          <button
            class="icon-button"
            type="button"
            :disabled="loggingOut"
            aria-label="退出登录"
            title="退出登录"
            @click="logout"
          >
            <LogOut :size="17" aria-hidden="true" />
          </button>
          <span v-if="logoutError" class="logout-error" role="alert">退出失败</span>
        </div>
      </div>
    </header>

    <main id="account-workspace" class="content-wrap admin-main">
      <section class="page-heading" aria-labelledby="admin-title">
        <div>
          <p class="page-kicker">访问控制 / 内部账号</p>
          <h1 id="admin-title">账号管理</h1>
          <p>创建平台账号、调整使用权限，并在需要时重置密码或撤销登录会话。</p>
        </div>
        <button v-if="!createExpanded" class="primary-command" type="button" @click="openCreate">
          <Plus :size="18" aria-hidden="true" />
          创建账号
        </button>
      </section>

      <section v-if="createExpanded" class="create-editor" aria-labelledby="create-title">
        <div class="editor-heading">
          <div>
            <p>新账号</p>
            <h2 id="create-title">授予平台访问权限</h2>
          </div>
          <button
            class="icon-button"
            type="button"
            :disabled="creating"
            aria-label="关闭创建账号表单"
            @click="closeCreate"
          >
            <X :size="18" aria-hidden="true" />
          </button>
        </div>

        <form class="create-form" novalidate @submit.prevent="submitCreate">
          <label class="field-control">
            <span>账号邮箱</span>
            <input
              v-model="createEmail"
              name="new-email"
              type="email"
              autocomplete="off"
              placeholder="name@example.com"
              :disabled="creating"
            />
          </label>
          <label class="field-control">
            <span>初始密码</span>
            <input
              v-model="createPassword"
              name="new-password"
              type="password"
              autocomplete="new-password"
              placeholder="12–128 个字符"
              :disabled="creating"
            />
          </label>
          <label class="check-control">
            <input v-model="createSuperuser" type="checkbox" :disabled="creating" />
            <span>
              <strong>超级用户</strong>
              <small>可管理账号并执行手动 Pipeline</small>
            </span>
          </label>
          <button class="submit-command" type="submit" :disabled="creating">
            <LoaderCircle v-if="creating" class="spin" :size="17" aria-hidden="true" />
            <Check v-else :size="17" aria-hidden="true" />
            {{ creating ? '正在创建' : '确认创建' }}
          </button>
          <p v-if="createError" class="editor-error" role="alert">{{ createError }}</p>
        </form>
      </section>

      <section class="account-summary" aria-label="账号概况">
        <span
          ><strong>{{ totalUsers }}</strong
          >全部账号</span
        >
        <span
          ><strong>{{ activeUsers }}</strong
          >启用</span
        >
        <span
          ><strong>{{ superusers }}</strong
          >超级用户</span
        >
        <span class="summary-note">密码与会话仅保存在服务端</span>
      </section>

      <p v-if="feedback" class="feedback" role="status">
        <Check :size="16" aria-hidden="true" />
        {{ feedback }}
      </p>

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
            @click="loadAccounts"
          >
            <RefreshCw :class="{ spin: loadState === 'loading' }" :size="15" aria-hidden="true" />
            刷新
          </button>
        </div>

        <div v-if="loadState === 'loading'" class="directory-state" role="status">
          <LoaderCircle class="spin" :size="20" aria-hidden="true" />
          正在读取账号目录
        </div>

        <div
          v-else-if="loadState === 'error'"
          class="directory-state directory-state-error"
          role="alert"
        >
          <span>{{ loadError }}</span>
          <button type="button" @click="loadAccounts">重新加载</button>
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

          <article
            v-for="user in users"
            :key="user.id"
            class="user-row"
            :class="{ 'environment-row': user.is_environment_admin }"
            role="row"
            :data-user-id="user.id"
          >
            <div class="user-identity" role="cell">
              <span class="user-avatar" aria-hidden="true">
                <ShieldCheck v-if="user.is_environment_admin" :size="17" />
                <UserRound v-else :size="17" />
              </span>
              <span class="user-copy">
                <strong>{{ user.email }}</strong>
                <small v-if="user.is_environment_admin" class="managed-note">
                  由部署 Secret 托管
                </small>
                <small v-else-if="user.id === authSession.user.value?.id">当前账号</small>
                <small v-else>数据库账号</small>
              </span>
            </div>

            <div class="status-cell" role="cell">
              <label
                class="switch-control"
                :class="{ 'switch-disabled': user.is_environment_admin }"
                :title="user.is_environment_admin ? '由部署 Secret 管理' : '允许或停止账号使用'"
              >
                <input
                  type="checkbox"
                  :checked="user.is_active"
                  :disabled="user.is_environment_admin || isBusy(user.id)"
                  :aria-label="`${user.email} 使用状态`"
                  :data-testid="`active-${user.id}`"
                  @change="changeActive(user, $event)"
                />
                <span aria-hidden="true"></span>
              </label>
              <small>{{ user.is_active ? '启用' : '停用' }}</small>
            </div>

            <div class="status-cell" role="cell">
              <label
                class="switch-control"
                :class="{ 'switch-disabled': user.is_environment_admin }"
                :title="
                  user.is_environment_admin ? '保底管理员必须保持超级用户' : '授予账号管理权限'
                "
              >
                <input
                  type="checkbox"
                  :checked="user.is_superuser"
                  :disabled="user.is_environment_admin || isBusy(user.id)"
                  :aria-label="`${user.email} 超级用户权限`"
                  :data-testid="`superuser-${user.id}`"
                  @change="changeSuperuser(user, $event)"
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
                :disabled="user.is_environment_admin || isBusy(user.id)"
                :title="user.is_environment_admin ? '请修改部署 Secret 后重启服务' : '重置密码'"
                :data-testid="`reset-${user.id}`"
                @click="openPasswordReset(user)"
              >
                <KeyRound :size="15" aria-hidden="true" />
                重置密码
              </button>
              <button
                type="button"
                :disabled="isBusy(user.id)"
                :data-testid="`sessions-${user.id}`"
                @click="revokeSessions(user)"
              >
                <RefreshCw :size="15" aria-hidden="true" />
                撤销会话
              </button>
            </div>

            <form
              v-if="resetUserId === user.id"
              class="reset-editor"
              @submit.prevent="submitPasswordReset(user)"
            >
              <label class="field-control">
                <span>为 {{ user.email }} 设置新密码</span>
                <input
                  v-model="resetPassword"
                  name="reset-password"
                  type="password"
                  autocomplete="new-password"
                  placeholder="12–128 个字符"
                  :disabled="isBusy(user.id)"
                />
              </label>
              <button class="submit-command" type="submit" :disabled="isBusy(user.id)">
                <LoaderCircle v-if="isBusy(user.id)" class="spin" :size="16" aria-hidden="true" />
                <Check v-else :size="16" aria-hidden="true" />
                确认重置
              </button>
              <button class="cancel-command" type="button" @click="cancelPasswordReset">
                取消
              </button>
              <p v-if="resetError" class="editor-error" role="alert">{{ resetError }}</p>
            </form>

            <p v-if="rowErrors[user.id]" class="row-error" role="alert">
              {{ rowErrors[user.id] }}
            </p>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.admin-shell {
  min-height: 100vh;
  background: var(--paper-50);
}

.admin-topbar {
  position: sticky;
  z-index: 10;
  top: 0;
  border-bottom: 1px solid var(--paper-300);
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(12px);
}

/* .topbar-inner 见 styles/components/topbar.css。 */
.topbar-actions,
.brand-lockup,
.account-identity,
.back-link {
  display: flex;
  align-items: center;
}

.brand-lockup {
  gap: 11px;
  color: inherit;
  text-decoration: none;
}

/* .brand-mark 见 styles/components/topbar.css。 */

/* .brand-copy 见 styles/components/topbar.css。 */

.brand-copy strong {
  font-family: var(--display-font);
  font-size: 1rem;
  font-weight: 760;
  line-height: 1.2;
}

.brand-copy small,
.account-identity {
  color: var(--ink-700);
  font-size: 0.72rem;
}

.topbar-actions {
  position: relative;
  gap: 12px;
}

.back-link {
  gap: 7px;
  padding-right: 16px;
  border-right: 1px solid var(--paper-300);
  color: var(--ink-700);
  font-size: 0.76rem;
  font-weight: 680;
  text-decoration: none;
}

.back-link:hover {
  color: var(--source-600);
}

.account-identity {
  max-width: 220px;
  gap: 7px;
}

.account-identity span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 本页通用的小图标幽灵按钮，退出登录与关闭表单两处复用。
   SearchComposer 的清空按钮曾同名但是 44px 实底填充，现已改名 .clear-button。 */
.icon-button {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--ink-700);
  background: transparent;
}

.icon-button:hover:not(:disabled) {
  border-color: var(--paper-300);
  color: var(--signal-600);
  background: var(--paper-100);
}

.icon-button:disabled {
  cursor: wait;
  opacity: 0.45;
}

.logout-error {
  position: absolute;
  top: calc(100% + 9px);
  right: 0;
  color: var(--danger-600);
  font-size: 0.7rem;
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

.page-kicker,
.editor-heading p,
.directory-heading p {
  color: var(--signal-600);
  font-size: 0.74rem;
  font-weight: 760;
}

.page-heading h1 {
  margin-top: 7px;
  font-family: var(--display-font);
  font-size: 2.45rem;
  font-weight: 780;
  line-height: 1.1;
}

.page-heading > div > p:last-child {
  max-width: 620px;
  margin-top: 13px;
  color: var(--ink-700);
  font-size: 0.91rem;
}

.primary-command,
.submit-command {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 42px;
  padding: 0 17px;
  border: 1px solid var(--ink-950);
  border-radius: var(--radius-sm);
  color: var(--paper-50);
  background: var(--ink-950);
  font-size: 0.81rem;
  font-weight: 720;
}

.primary-command:hover,
.submit-command:hover:not(:disabled) {
  border-color: var(--source-600);
  background: var(--source-600);
}

.submit-command:disabled {
  cursor: wait;
  opacity: 0.5;
}

.create-editor {
  padding: 25px 28px 27px;
  border-top: 3px solid var(--source-500);
  border-bottom: 1px solid var(--paper-300);
  background: var(--paper-100);
}

.editor-heading,
.directory-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.editor-heading h2,
.directory-heading h2 {
  margin-top: 4px;
  font-family: var(--display-font);
  font-size: 1.24rem;
  font-weight: 760;
}

.create-form {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) minmax(200px, 0.8fr) auto;
  align-items: end;
  gap: 18px;
  margin-top: 22px;
}

.field-control {
  display: grid;
  gap: 7px;
  color: var(--ink-800);
  font-size: 0.75rem;
  font-weight: 700;
}

.field-control input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  color: var(--ink-950);
  background: var(--paper-50);
  font-size: 0.84rem;
  outline: none;
}

.field-control input:focus {
  border-color: var(--source-500);
  box-shadow: 0 0 0 3px var(--source-100);
}

.check-control {
  display: flex;
  align-items: center;
  min-height: 42px;
  gap: 10px;
  color: var(--ink-800);
  font-size: 0.76rem;
}

.check-control input {
  width: 17px;
  height: 17px;
  accent-color: var(--source-600);
}

.check-control span {
  display: grid;
  gap: 1px;
}

.check-control small {
  color: var(--ink-500);
  font-size: 0.67rem;
  font-weight: 450;
}

.editor-error {
  grid-column: 1 / -1;
  padding: 9px 11px;
  border-left: 3px solid var(--danger-600);
  color: var(--danger-600);
  background: var(--danger-100);
  font-size: 0.76rem;
}

.account-summary {
  display: flex;
  align-items: center;
  gap: 28px;
  min-height: 57px;
  border-bottom: 1px solid var(--paper-300);
  color: var(--ink-700);
  font-family: var(--mono-font);
  font-size: 0.68rem;
}

.account-summary span {
  display: inline-flex;
  align-items: baseline;
  gap: 7px;
  white-space: nowrap;
}

.account-summary strong {
  color: var(--ink-950);
  font-family: var(--display-font);
  font-size: 1.08rem;
}

.account-summary .summary-note {
  margin-left: auto;
  color: var(--ink-500);
}

.feedback {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
  padding: 10px 12px;
  border-left: 3px solid var(--source-500);
  color: var(--source-600);
  background: var(--source-100);
  font-size: 0.77rem;
}

.directory {
  margin-top: 36px;
}

.directory-heading {
  padding-bottom: 15px;
}

.refresh-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 0;
  color: var(--ink-700);
  background: transparent;
  font-size: 0.73rem;
  font-weight: 680;
}

.refresh-button:hover:not(:disabled) {
  color: var(--source-600);
}

.directory-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  gap: 10px;
  border-top: 1px solid var(--paper-300);
  border-bottom: 1px solid var(--paper-300);
  color: var(--ink-500);
  font-size: 0.82rem;
}

.directory-state-error {
  flex-direction: column;
  color: var(--danger-600);
}

.directory-state-error button {
  padding: 0;
  border: 0;
  color: var(--source-600);
  background: transparent;
  font-weight: 700;
}

.user-table {
  border-top: 1px solid var(--ink-950);
}

.user-table-head,
.user-row {
  display: grid;
  grid-template-columns:
    minmax(235px, 1.7fr) minmax(115px, 0.7fr) minmax(125px, 0.8fr) minmax(115px, 0.7fr)
    minmax(220px, 1.3fr);
  gap: 18px;
}

.user-table-head {
  padding: 10px 14px;
  border-bottom: 1px solid var(--paper-300);
  color: var(--ink-500);
  font-family: var(--mono-font);
  font-size: 0.65rem;
  text-transform: uppercase;
}

.user-row {
  position: relative;
  align-items: center;
  min-height: 88px;
  padding: 15px 14px;
  border-bottom: 1px solid var(--paper-300);
  background: var(--paper-50);
}

.user-row:hover {
  background: var(--paper-100);
}

.environment-row {
  border-left: 3px solid var(--signal-500);
  background-image: repeating-linear-gradient(
    -45deg,
    transparent,
    transparent 8px,
    rgba(211, 218, 215, 0.16) 8px,
    rgba(211, 218, 215, 0.16) 9px
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
  border: 1px solid var(--paper-300);
  border-radius: 50%;
  color: var(--source-600);
  background: var(--paper-50);
}

.environment-row .user-avatar {
  border-color: var(--signal-500);
  color: var(--signal-600);
}

.user-copy,
.created-cell {
  display: grid;
  gap: 3px;
}

.user-copy strong {
  overflow: hidden;
  color: var(--ink-950);
  font-size: 0.8rem;
  font-weight: 710;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-copy small,
.created-cell small,
.status-cell small {
  color: var(--ink-500);
  font-size: 0.66rem;
}

.user-copy .managed-note {
  color: var(--signal-600);
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
  border: 1px solid var(--paper-300);
  border-radius: 10px;
  background: var(--paper-200);
  transition: background 140ms ease;
}

.switch-control > span::after {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--paper-50);
  box-shadow: 0 1px 3px rgba(24, 33, 31, 0.25);
  content: '';
  transition: transform 140ms ease;
}

.switch-control input:checked + span {
  border-color: var(--source-500);
  background: var(--source-500);
}

.switch-control input:checked + span::after {
  transform: translateX(14px);
}

.switch-control input:focus-visible + span {
  outline: 3px solid rgba(47, 121, 110, 0.32);
  outline-offset: 2px;
}

.switch-disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.created-cell span {
  color: var(--ink-800);
  font-family: var(--mono-font);
  font-size: 0.68rem;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.row-actions button,
.cancel-command {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  gap: 6px;
  padding: 0 9px;
  border: 1px solid var(--paper-300);
  border-radius: var(--radius-sm);
  color: var(--ink-700);
  background: var(--paper-50);
  font-size: 0.7rem;
  font-weight: 650;
}

.row-actions button:hover:not(:disabled),
.cancel-command:hover {
  border-color: var(--source-500);
  color: var(--source-600);
}

.row-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.reset-editor {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: minmax(260px, 1fr) auto auto;
  align-items: end;
  gap: 10px;
  padding: 16px 0 3px 45px;
  border-top: 1px dashed var(--paper-300);
}

.reset-editor .submit-command {
  min-height: 42px;
}

.reset-editor .cancel-command {
  min-height: 42px;
  padding: 0 13px;
}

.row-error {
  grid-column: 1 / -1;
  padding: 8px 10px;
  border-left: 3px solid var(--danger-600);
  color: var(--danger-600);
  background: var(--danger-100);
  font-size: 0.73rem;
}

/* 本页刻意用 800ms，与 styles/components/motion.css 的共享 900ms 不同；
   scoped 样式未分层，恒定覆盖 @layer components。 */
.spin {
  animation: spin 800ms linear infinite;
}

@media (max-width: 1040px) {
  .create-form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .create-form .submit-command {
    justify-self: start;
  }

  .user-table-head {
    display: none;
  }

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

@media (max-width: 720px) {
  .topbar-inner {
    min-height: 62px;
  }

  .brand-copy small,
  .account-identity {
    display: none;
  }

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

  .create-editor {
    padding: 22px 17px 24px;
  }

  .create-form {
    grid-template-columns: 1fr;
  }

  .account-summary {
    display: grid;
    grid-template-columns: repeat(3, auto);
    gap: 10px 18px;
    padding: 13px 0;
  }

  .account-summary .summary-note {
    grid-column: 1 / -1;
    margin-left: 0;
  }

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

  .reset-editor {
    grid-template-columns: 1fr auto;
    padding: 15px 0 1px;
  }

  .reset-editor .field-control,
  .reset-editor .editor-error {
    grid-column: 1 / -1;
  }
}

@media (max-width: 430px) {
  .account-summary {
    grid-template-columns: repeat(2, auto);
  }

  .user-row {
    grid-template-columns: 1fr;
  }

  .user-identity,
  .row-actions,
  .reset-editor {
    grid-column: 1;
  }

  .row-actions button {
    flex: 1 1 auto;
    justify-content: center;
  }

  .reset-editor {
    grid-template-columns: 1fr;
  }

  .reset-editor .submit-command,
  .reset-editor .cancel-command {
    width: 100%;
  }
}
</style>
