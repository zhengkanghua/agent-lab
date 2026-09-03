import { resolveErrorCopy } from '@/api/error-copy'

/*
 * 定时任务管理的错误文案，与 features/user-admin/model/admin-error.ts 同一模式：
 * 失败一律由后端 code 区分，只提供 byCode 一张表；每条消息贴在它对应的那一行或
 * 表单旁边，上下文由位置给出，所以一句话即可。
 *
 * 错误码来自后端 api/scheduled_jobs.py 的领域错误表与 error_contract 的
 * SCHEDULED_JOB_ERROR_RULES；两边一起改，漏了这边只会退到兜底文案，不会崩。
 */

const MESSAGE_BY_CODE: Readonly<Partial<Record<string, string>>> = {
  scheduled_job_not_found: '该任务已不存在，请刷新列表。',
  scheduled_job_key_conflict: '同名任务标识已存在，请换一个任务标识。',
  scheduled_job_already_running: '上一轮还在执行中，等它结束后再触发。',
  scheduled_job_invalid_cron: 'cron 表达式无效，需要 5 段式 cron（分 时 日 月 周）。',
  scheduled_job_invalid_params: '任务参数与所选类型不匹配，请检查取值范围。',
  scheduled_job_unknown_type: '未知的任务类型，请刷新页面后重试。',
  scheduled_job_database_unavailable: '定时任务存储暂时不可用，请稍后重试。',
  permission_denied: '当前账号没有管理权限。',
  invalid_request: '提交内容不符合定时任务要求，请检查后重试。',
}

export function presentJobError(cause: unknown, fallback: string): string {
  return resolveErrorCopy(cause, { byCode: MESSAGE_BY_CODE, fallback })
}
