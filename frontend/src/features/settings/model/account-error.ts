import type { ApiError } from '@/api/client'

const ACCOUNT_ERROR_COPY: Record<string, string> = {
  current_password_invalid: '当前密码不正确。',
  invalid_password: '密码必须包含 12 到 128 个字符，且不能与登录邮箱完全相同。',
  environment_admin_protected: '环境托管超级用户必须通过服务端密钥修改。',
  user_admin_database_unavailable: '账号管理服务暂时不可用，请稍后重试。',
  // 「服务器内部错误」是给排查的人看的，不是给改密码的人看的：说清是哪一块出了状况，
  // 用户才知道该等一会儿还是该找管理员。这一句原来还用了半角逗号，
  // 与 user-admin 那份错误表的标点也不一致。
  internal_server_error: '账号服务出错了，请稍后重试。',
  invalid_request: '请求参数无效。',
}

export function getAccountErrorCopy(error: ApiError): string {
  if (error.code && error.code in ACCOUNT_ERROR_COPY) {
    return ACCOUNT_ERROR_COPY[error.code]
  }
  // 兜底也要说清是什么没做成。原来只写「操作失败」——这一页只有「改自己的密码」一件事，
  // 含糊的代价是用户不知道要不要再点一次。
  return error.detail || '账号操作没有完成，请稍后重试。'
}
