import { onScopeDispose, ref } from 'vue'
import { ApiError } from '@/api/client'
import { isRecord, isUuid } from '@/api/json-guards'
import { triggerScheduledJob } from '@/api/scheduled-jobs'
import { retryTask, submitPipeline, type PipelineRequest, type TaskAcceptedDto } from '@/api/tasks'
import { presentJobError } from '../model/job-error'

export type TaskCommand =
  | { kind: 'scheduled'; jobId: string }
  | { kind: 'retry'; runId: string }
  | { kind: 'pipeline'; params: PipelineRequest }

export interface PendingSubmission {
  key: string
  label: string
  createdAt: string
  command: TaskCommand
}

export const selectedRunStorageKey = (accountId: string) => `task-selected-run:${accountId}`

function commandSlot(command: TaskCommand): string {
  if (command.kind === 'scheduled') return `scheduled:${command.jobId}`
  if (command.kind === 'retry') return `retry:${command.runId}`
  return 'pipeline'
}

function isPending(value: unknown): value is PendingSubmission {
  if (
    !isRecord(value) ||
    !isUuid(value.key) ||
    typeof value.label !== 'string' ||
    typeof value.createdAt !== 'string' ||
    !isRecord(value.command)
  )
    return false
  const command = value.command
  return (
    (command.kind === 'scheduled' && isUuid(command.jobId)) ||
    (command.kind === 'retry' && isUuid(command.runId)) ||
    (command.kind === 'pipeline' &&
      isRecord(command.params) &&
      ['limit_per_source', 'batch_size', 'stale_after_minutes'].every((key) =>
        Number.isInteger((command.params as Record<string, unknown>)[key]),
      ))
  )
}

/** 发请求前保存身份与原内容；回执未知时只允许确认同一个请求，跨页仍可继续。 */
export function useTaskSubmissions(
  accountId: string,
  onAccepted: (receipt: TaskAcceptedDto) => void,
) {
  const prefix = `task-request:${accountId}:`
  const pending = ref<PendingSubmission[]>([])
  const busyKey = ref('')
  const error = ref('')
  const conflictRunId = ref('')
  const feedback = ref('')
  let active = true
  onScopeDispose(() => {
    active = false
  })

  function reload(): void {
    try {
      const items: PendingSubmission[] = []
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i)
        if (!key?.startsWith(prefix)) continue
        const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? 'null')
        if (isPending(value)) items.push(value)
      }
      pending.value = items.sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    } catch {
      error.value = '浏览器无法读取待确认请求，请恢复此站点的会话存储后再提交。'
    }
  }
  reload()

  function forget(item: PendingSubmission): void {
    try {
      sessionStorage.removeItem(prefix + item.key)
    } catch {
      /* 保留原身份，再次确认仍安全。 */
    }
    pending.value = pending.value.filter((record) => record.key !== item.key)
  }

  async function resend(item: PendingSubmission): Promise<TaskAcceptedDto | undefined> {
    if (busyKey.value) return
    busyKey.value = item.key
    error.value = ''
    conflictRunId.value = ''
    feedback.value = ''
    try {
      const command = item.command
      const receipt =
        command.kind === 'scheduled'
          ? await triggerScheduledJob(command.jobId, item.key)
          : command.kind === 'retry'
            ? await retryTask(command.runId, item.key)
            : await submitPipeline(command.params, item.key)
      // 每个请求单独存储，旧页面迟到的响应不会覆盖新页面保存的其他请求。
      try {
        sessionStorage.setItem(selectedRunStorageKey(accountId), receipt.run_id)
      } catch {
        /* 页面仍显示编号。 */
      }
      forget(item)
      if (active) {
        feedback.value = receipt.details_expired
          ? `原执行 ${receipt.run_id} 的详情已过期，此次未新建执行。`
          : `已受理，执行编号：${receipt.run_id}。`
        onAccepted(receipt)
      }
      return receipt
    } catch (cause) {
      const rejected =
        cause instanceof ApiError && [400, 401, 403, 404, 409, 410, 422].includes(cause.status)
      if (rejected) forget(item)
      if (active) {
        error.value = rejected
          ? presentJobError(cause, '提交被拒绝，请检查任务状态与参数。')
          : '受理情况待确认。请使用下方“确认受理”，沿用原请求标识和内容查询回执。'
        conflictRunId.value = cause instanceof ApiError ? (cause.runId ?? '') : ''
      }
    } finally {
      busyKey.value = ''
    }
  }

  function hasPending(command: TaskCommand): boolean {
    return pending.value.some((item) => commandSlot(item.command) === commandSlot(command))
  }

  async function submit(command: TaskCommand, label: string): Promise<TaskAcceptedDto | undefined> {
    if (busyKey.value) return
    reload()
    if (hasPending(command)) {
      error.value = '此操作还有待确认请求，请先确认原请求的受理结果。'
      return
    }
    // JSON 快照避免用户在等待响应时修改表单，从而改变下一次确认的请求内容。
    const item: PendingSubmission = JSON.parse(
      JSON.stringify({
        key: crypto.randomUUID(),
        label,
        command,
        createdAt: new Date().toISOString(),
      }),
    )
    try {
      sessionStorage.setItem(prefix + item.key, JSON.stringify(item))
    } catch {
      error.value = '浏览器无法保存请求标识，本次尚未提交。请允许此站点使用会话存储后重试。'
      return
    }
    pending.value = [...pending.value, item]
    return resend(item)
  }

  return { pending, busyKey, error, feedback, conflictRunId, hasPending, submit, resend }
}

export type TaskSubmissions = ReturnType<typeof useTaskSubmissions>
