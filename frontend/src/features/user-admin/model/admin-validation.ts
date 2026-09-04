/**
 * 账号管理表单的本地校验。纯函数，不做任何网络请求。
 *
 * 密码长度常量统一定义在 `shared/model/password.ts`（账号自助改密与管理端镜像的是
 * 同一套后端 Field 约束），本文件只保留管理端表单自己的校验函数。
 */

import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '@/shared/model/password'

/** 目录的三种加载态。ready 之外的两种都不渲染表格。 */
export type DirectoryLoadState = 'loading' | 'ready' | 'error'

/**
 * 邮箱格式。
 *
 * 刻意宽松：这里只拦「一眼就不是邮箱」的输入，真正的判定归后端的 EmailStr。
 * 前端写严会把合法但少见的地址挡在外面，而用户看不到后端本来会放行。
 */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** 返回空串表示通过，与调用点「有文案就显示」的写法对齐。 */
export function validateCredentials(email: string, password: string): string {
  if (!EMAIL_SHAPE.test(email)) return '请输入有效的账号邮箱。'
  return validatePassword(password)
}

export function validatePassword(password: string): string {
  if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
    return `密码长度需要在 ${PASSWORD_MIN_LENGTH} 到 ${PASSWORD_MAX_LENGTH} 个字符之间。`
  }
  return ''
}
