import { resolveErrorCopy } from '@/api/error-copy'

/*
 * 账号管理的错误文案。
 *
 * 这一页与 Agent 页、检索页不同：失败只用一句话表述，没有「标题 + 下一步」两段，
 * 因为每条消息都贴在它对应的那一行或那个表单旁边，上下文已经由位置给出。
 * 所以这里的表是 `code → string`，而不是 `code → ErrorCopy`。
 *
 * 失败一律由后端 code 区分，与状态码无关（`invalid_password` 是 422、
 * `last_superuser_protected` 是 409，但两者要说的话完全不同），因此只提供 byCode 一张表。
 */

// 错误码来自后端 api/error_contract.py 的账号管理规则；新增码时两边一起改，
// 漏了这边只会退到兜底文案，不会崩，但用户就看不到「该怎么办」了。
const MESSAGE_BY_CODE: Readonly<Partial<Record<string, string>>> = {
  user_already_exists: '该邮箱已经存在账号。',
  invalid_password: '密码不符合安全要求：需要 12 到 128 个字符，且不能与账号邮箱相同。',
  environment_admin_protected: '保底管理员由部署 Secret 托管，不能在网页中修改。',
  last_superuser_protected: '不能停用或降级最后一个启用的超级用户。',
  user_not_found: '该账号已不存在，请刷新列表。',
  permission_denied: '当前账号没有管理权限。',
  invalid_request: '提交内容不符合账号管理要求，请检查后重试。',
}

/**
 * 兜底文案按调用点传入，因为「读列表失败」和「改密码失败」该说的下一步动作不一样。
 */
export function presentAdminError(cause: unknown, fallback: string): string {
  return resolveErrorCopy(cause, { byCode: MESSAGE_BY_CODE, fallback })
}
