import { onScopeDispose, ref } from 'vue'
import { createUser, type UserAdminDto } from '@/api/user-admin'
import { presentAdminError } from '../model/admin-error'
import { validateCredentials } from '../model/admin-validation'

export interface UseAccountCreateFormOptions {
  /** 创建成功后交给目录并列。表单自己不碰账号列表。 */
  onCreated: (created: UserAdminDto) => void
  /** 展开表单时清掉上一条成功提示——那条说的是上一个账号，留着会被当成这一次的结果。 */
  onOpen: () => void
}

/**
 * 创建账号的表单状态。
 *
 * 与目录分开是因为两者的生命周期无关：表单可以一直收着，目录照常刷新与改行。
 * 唯一的耦合是「创建成功之后新行要出现在列表里」，通过 onCreated 单向传出去。
 */
export function useAccountCreateForm(options: UseAccountCreateFormOptions) {
  const expanded = ref(false)
  const submitting = ref(false)
  const email = ref('')
  const password = ref('')
  const superuser = ref(false)
  const error = ref('')

  function open(): void {
    expanded.value = true
    error.value = ''
    options.onOpen()
  }

  /** 提交中不许关：请求还在跑，关掉表单会让人以为已经取消了。 */
  function close(): void {
    if (submitting.value) return
    reset()
  }

  function reset(): void {
    expanded.value = false
    error.value = ''
    email.value = ''
    password.value = ''
    superuser.value = false
  }

  async function submit(): Promise<void> {
    if (submitting.value) return

    const trimmedEmail = email.value.trim()
    const validation = validateCredentials(trimmedEmail, password.value)
    if (validation) {
      error.value = validation
      return
    }

    submitting.value = true
    error.value = ''
    try {
      const created = await createUser({
        email: trimmedEmail,
        password: password.value,
        isSuperuser: superuser.value,
      })
      // 不走 close()：那里的 submitting 守卫此刻恒为真，会把表单留在展开状态。
      reset()
      options.onCreated(created)
    } catch (cause) {
      error.value = presentAdminError(cause, '账号创建失败，请稍后重试。')
    } finally {
      submitting.value = false
    }
  }

  function clearSensitiveInput(): void {
    password.value = ''
  }

  onScopeDispose(clearSensitiveInput)

  return {
    expanded,
    submitting,
    email,
    password,
    superuser,
    error,
    open,
    close,
    submit,
    clearSensitiveInput,
  }
}
