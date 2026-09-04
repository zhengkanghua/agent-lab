import { ref, computed } from 'vue'
import { changeOwnPassword, type PasswordChangeRequest } from '@/api/account'
import { getAccountErrorCopy } from '../model/account-error'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '@/shared/model/password'
import type { ApiError } from '@/api/client'

export interface PasswordChangeFormState {
  currentPassword: string
  newPassword: string
  confirmPassword: string
}

interface ValidationError {
  field: string
  message: string
}

export function usePasswordChangeForm() {
  const form = ref<PasswordChangeFormState>({
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  })

  const validationErrors = ref<ValidationError[]>([])
  const isPending = ref(false)
  const errorMessage = ref<string | null>(null)
  const successMessage = ref<string | null>(null)

  const canSubmit = computed(() => {
    return (
      !isPending.value &&
      form.value.currentPassword.length > 0 &&
      form.value.newPassword.length > 0 &&
      form.value.confirmPassword.length > 0
    )
  })

  function validate(): boolean {
    validationErrors.value = []

    if (
      form.value.newPassword.length < PASSWORD_MIN_LENGTH ||
      form.value.newPassword.length > PASSWORD_MAX_LENGTH
    ) {
      validationErrors.value.push({
        field: 'newPassword',
        message: `密码长度需要在 ${PASSWORD_MIN_LENGTH} 到 ${PASSWORD_MAX_LENGTH} 个字符之间。`,
      })
    }

    if (form.value.newPassword !== form.value.confirmPassword) {
      validationErrors.value.push({
        field: 'confirmPassword',
        message: '两次输入的密码不一致。',
      })
    }

    return validationErrors.value.length === 0
  }

  async function submit(): Promise<void> {
    if (!canSubmit.value) return

    errorMessage.value = null
    successMessage.value = null

    if (!validate()) {
      return
    }

    isPending.value = true

    try {
      const request: PasswordChangeRequest = {
        current_password: form.value.currentPassword,
        new_password: form.value.newPassword,
      }

      await changeOwnPassword(request)

      successMessage.value = '密码修改成功,其他设备的登录已失效。'
      form.value.currentPassword = ''
      form.value.newPassword = ''
      form.value.confirmPassword = ''
      validationErrors.value = []
    } catch (error) {
      errorMessage.value = getAccountErrorCopy(error as ApiError)
    } finally {
      isPending.value = false
    }
  }

  return {
    form,
    validationErrors,
    canSubmit,
    submit,
    isPending,
    errorMessage,
    successMessage,
  }
}
