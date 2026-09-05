import type { ApiError } from '@/api/client'

const ACCOUNT_ERROR_COPY: Record<string, string> = {
  current_password_invalid: '当前密码不正确。',
  invalid_password: '密码必须包含 12 到 128 个字符，且不能与登录邮箱完全相同。',
  environment_admin_protected: '环境托管的管理员账号必须通过服务端密钥修改。',
  user_admin_database_unavailable: '账号管理服务暂时不可用，请稍后重试。',
  internal_server_error: '服务器内部错误,请稍后重试。',
  invalid_request: '请求参数无效。',
}

export function getAccountErrorCopy(error: ApiError): string {
  if (error.code && error.code in ACCOUNT_ERROR_COPY) {
    return ACCOUNT_ERROR_COPY[error.code]
  }
  return error.detail || '操作失败,请稍后重试。'
}
