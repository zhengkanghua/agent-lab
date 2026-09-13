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
  scheduled_job_already_running: '此配置已有未结束的任务执行，请查看该执行。',
  task_operation_conflict: '当前状态不能执行此操作，请刷新任务详情。',
  task_retry_unavailable: '只有保留完整参数且已结束的失败执行可以重试。',
  task_run_not_found: '任务执行不存在，请核对执行编号。',
  task_run_expired: '执行详情已过保留期，原编号仍保留，不能再以旧参数重试。',
  task_request_conflict: '请求标识已用于不同内容，请核对原请求。',
  task_service_unavailable: '任务服务暂时不可用，请稍后确认受理结果。',
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
