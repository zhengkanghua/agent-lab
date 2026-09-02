import type { PasswordChangeFormState } from '../composables/usePasswordChangeForm'

export interface ValidationFailure {
  field: keyof PasswordChangeFormState
  message: string
}

export function validatePasswordChangeRequest(form: PasswordChangeFormState): ValidationFailure[] {
  const errors: ValidationFailure[] = []

  if (form.currentPassword.length === 0) {
    errors.push({ field: 'currentPassword', message: '当前密码不能为空。' })
  }

  if (form.newPassword.length < 12) {
    errors.push({ field: 'newPassword', message: '新密码至少需要 12 个字符。' })
  } else if (form.newPassword.length > 128) {
    errors.push({ field: 'newPassword', message: '新密码不能超过 128 个字符。' })
  }

  if (form.newPassword !== form.confirmPassword) {
    errors.push({ field: 'confirmPassword', message: '两次输入的新密码不一致。' })
  }

  return errors
}
