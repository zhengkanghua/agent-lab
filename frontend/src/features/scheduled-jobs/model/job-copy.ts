import type { JobRunDto, ScheduledJobDto, ScheduledJobTaskType } from '@/api/scheduled-jobs'

/*
 * 定时任务的展示模型：类型/状态/触发方式的中文文案、北京时间格式化、统计摘要。
 * 术语对齐仓库根 CONTEXT.md：定时任务（ScheduledJob）、任务执行（JobRun）。
 * 代码标识符沿用后端字段名不改，只有给人看的文案在这里翻译。
 */

export const TASK_TYPE_LABEL: Readonly<Record<ScheduledJobTaskType, string>> = {
  freshrss_sync: 'FreshRSS 同步',
  index_pending: '向量索引',
  prune_old_documents: '旧文档清理',
}

export const RUN_STATUS_LABEL: Readonly<Record<JobRunDto['status'], string>> = {
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  skipped: '已跳过',
}

export const TRIGGER_TYPE_LABEL: Readonly<Record<JobRunDto['trigger_type'], string>> = {
  scheduled: '定时',
  manual: '手动',
}

/*
 * 后端把 task_type / status / trigger_type 声明为 string（注册表是代码契约，不在
 * OpenAPI 里收敛成枚举），所以展示层用「安全索引」函数兜底：未知值原样展示，
 * 后端加类型时前端不至于渲染出 undefined。
 */
export function taskTypeLabel(taskType: string): string {
  return TASK_TYPE_LABEL[taskType as ScheduledJobTaskType] ?? taskType
}

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABEL[status as JobRunDto['status']] ?? status
}

export function executionStatusLabel(run: JobRunDto): string {
  if (run.needs_attention) return '待核实'
  if (
    run.status === 'succeeded' &&
    ['failed_source_count', 'failed_count', 'failed_batches'].some(
      (key) => Number(run.stats[key] ?? 0) > 0,
    )
  )
    return '已结束，有失败项'
  if (run.status === 'succeeded' && run.stats.resource_close_error) return '业务完成，资源关闭失败'
  return runStatusLabel(run.status)
}

export function triggerTypeLabel(triggerType: string): string {
  return TRIGGER_TYPE_LABEL[triggerType as JobRunDto['trigger_type']] ?? triggerType
}

/** 北京时间、固定格式（Q6 共识）：管理页要精确，不做相对时间。 */
const BEIJING_PARTS_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

/**
 * 把 UTC ISO8601 字符串格式化成 `YYYY-MM-DD HH:mm:ss`（北京时间）。
 *
 * 用 formatToParts 自己拼而不是直接用 formatter.format：zh-CN 的 format 输出
 * 「2026/09/03 09:00:00」，斜杠分隔不是共识里定的形状。解析不了的输入原样返回，
 * 让异常数据可见，而不是悄悄显示成 Invalid Date。
 */
export function formatBeijingTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const parts = BEIJING_PARTS_FORMATTER.formatToParts(date)
  const value = (type: string): string => parts.find((part) => part.type === type)?.value ?? ''
  return `${value('year')}-${value('month')}-${value('day')} ${value('hour')}:${value('minute')}:${value('second')}`
}

/** 上次执行摘要里的状态短语；空字符串表示「还没有执行过」。 */
export function formatLastRunSummary(job: ScheduledJobDto): string {
  const lastRun = job.active_run ?? job.last_run
  if (lastRun === null) return '尚未执行'
  const status = executionStatusLabel(lastRun)
  const finished =
    lastRun.finished_at !== null
      ? formatBeijingTime(lastRun.finished_at)
      : lastRun.status === 'running' && !lastRun.needs_attention
        ? '进行中'
        : formatBeijingTime(lastRun.started_at)
  return `${status} · ${finished}`
}

/*
 * 后端批次级失败时把 ``error_reason``（异常自带的稳定脱敏枚举，如 login_rejected）
 * 写进 stats。这里给出人话文案；未知值原样展示，后端加枚举时前端不至于渲染出 undefined。
 */
const ERROR_REASON_LABEL: Readonly<Record<string, string>> = {
  execution_interrupted: '任务执行已中断，结果需要核实',
  owner_confirmed_stopped: '已确认执行者停止，业务结果未确认',
  login_rejected: 'FreshRSS 登录被拒绝（API 凭据无效或被停用）',
  login_no_token: 'FreshRSS 登录响应缺少令牌（可能被反代/WAF 拦截）',
  request_rejected: 'FreshRSS API 请求被拒绝',
  login_timeout: 'FreshRSS 登录超时',
  request_timeout: 'FreshRSS 请求超时',
  login_unreachable: '无法连接 FreshRSS（登录阶段）',
  request_unreachable: '无法连接 FreshRSS（请求阶段）',
  login_http_error: 'FreshRSS 登录返回异常状态',
  request_http_error: 'FreshRSS 返回异常状态',
}

export function errorReasonLabel(reason: string): string {
  return ERROR_REASON_LABEL[reason] ?? reason
}

/**
 * 一条任务执行的统计摘要。
 *
 * stats 是后端按手动流水线口径给的脱敏统计：数量字段 + 按异常类型聚合的 failures；
 * skipped 记录只有 reason，批次级失败记录只有 error_reason。这里按字段名拼人话，
 * 不认识的字段忽略——后端加字段时前端不至于渲染出原始 JSON。
 */
export function formatRunStats(run: JobRunDto): string {
  if (run.status === 'skipped') {
    const reasons: Record<string, string> = {
      previous_run_still_running: '上一轮尚未结束，本轮按策略跳过',
      missed_fire_time: '已错过执行时间，本次不补执行',
      trigger_still_pending: '上次触发尚未受理，本次跳过',
    }
    return reasons[String(run.stats.reason)] ?? '本次触发已跳过'
  }
  const stats = run.stats
  const fragments: string[] = []
  if (run.needs_attention) fragments.push('任务执行或写入结果待核实')

  if (run.status === 'failed') {
    const reason = stats.error_reason
    if (typeof reason === 'string') {
      fragments.push(`失败原因：${errorReasonLabel(reason)}`)
    }
    if (fragments.length === 0) fragments.push(`本轮执行失败（${run.error_type ?? '未知原因'}）`)
  }

  if (typeof stats.synchronized_document_count === 'number') {
    fragments.push(`同步文档 ${stats.synchronized_document_count}`)
  }
  if (typeof stats.failed_source_count === 'number' && stats.failed_source_count > 0) {
    fragments.push(`失败来源 ${stats.failed_source_count}`)
  }
  if (typeof stats.indexed_count === 'number') {
    fragments.push(`已索引 ${stats.indexed_count}`)
  }
  if (typeof stats.failed_count === 'number' && stats.failed_count > 0)
    fragments.push(`失败文档 ${stats.failed_count}`)
  if (typeof stats.documents_deleted === 'number')
    fragments.push(`${stats.dry_run ? '预计删除' : '已删除'}文档 ${stats.documents_deleted}`)
  if (typeof stats.qdrant_points_deleted === 'number')
    fragments.push(
      `${stats.dry_run ? '预计涉及' : '删除前匹配'}片段 ${stats.qdrant_points_deleted}`,
    )
  if (typeof stats.failed_documents === 'number' && stats.failed_documents > 0)
    fragments.push(`未完成文档 ${stats.failed_documents}`)
  if (stats.resource_close_error)
    fragments.push(`资源关闭失败：${String(stats.resource_close_error)}`)
  if (typeof stats.candidate_count === 'number') {
    fragments.push(`候选 ${stats.candidate_count}`)
  }
  if (typeof stats.requeued_stale_count === 'number' && stats.requeued_stale_count > 0) {
    fragments.push(`回收超时 ${stats.requeued_stale_count}`)
  }
  if (typeof stats.skipped_count === 'number' && stats.skipped_count > 0) {
    fragments.push(`竞争跳过 ${stats.skipped_count}`)
  }

  const failures = stats.failures
  if (failures !== null && typeof failures === 'object' && !Array.isArray(failures)) {
    const entries = Object.entries(failures).filter(([, count]) => typeof count === 'number')
    if (entries.length > 0) {
      fragments.push(`失败：${entries.map(([type, count]) => `${type}×${count}`).join('、')}`)
    }
  }

  if (fragments.length > 0) return fragments.join(' · ')
  return run.status === 'running' ? '等待执行结果' : '本轮无变更'
}
