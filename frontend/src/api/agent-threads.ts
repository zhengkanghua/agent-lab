import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isNonNegativeInteger, isRecord, isUuid } from './json-guards'

export type AgentThreadSummaryDto = components['schemas']['AgentThreadSummary']
export type AgentThreadListDto = components['schemas']['AgentThreadListResponse']
export type AgentThreadMessagesDto = components['schemas']['AgentThreadMessagesResponse']
export type AgentReplayTurnDto = components['schemas']['AgentReplayTurn']
export type AgentReplayTraceDto = components['schemas']['AgentReplayTrace']
export type AgentThreadDeletionDto = components['schemas']['AgentThreadDeletionResponse']

export interface ListAgentThreadsOptions {
  limit?: number
  offset?: number
  signal?: AbortSignal
}

/**
 * 分页读取当前账号的会话列表。
 *
 * 后端只返回自己的会话，所以这里不做任何归属过滤——在前端过滤等于把访问控制搬到客户端，
 * 数据其实已经发到浏览器了。
 */
export async function listAgentThreads({
  limit,
  offset,
  signal,
}: ListAgentThreadsOptions = {}): Promise<AgentThreadListDto> {
  const query = new URLSearchParams()
  if (limit !== undefined) query.set('limit', String(limit))
  if (offset !== undefined) query.set('offset', String(offset))
  const suffix = query.size > 0 ? `?${query.toString()}` : ''

  const response = await requestJson<unknown>(`/agent/threads${suffix}`, {
    method: 'GET',
    signal,
  })
  if (
    !isRecord(response) ||
    !Array.isArray(response.items) ||
    !response.items.every(isThreadSummary) ||
    !isNonNegativeInteger(response.total)
  ) {
    throw invalidThreadResponse('会话服务返回的会话列表格式不正确。')
  }
  return response as unknown as AgentThreadListDto
}

/**
 * 读取一个会话已经存下的历史问答。
 *
 * 会话不存在或不属于当前账号时后端返回 404，``requestJson`` 会抛出带
 * ``agent_thread_not_found`` 的 ``ApiError``，文案由 ``model/agent-error.ts`` 决定。
 */
export async function getAgentThreadMessages(
  threadId: string,
  signal?: AbortSignal,
): Promise<AgentThreadMessagesDto> {
  const response = await requestJson<unknown>(
    `/agent/threads/${encodeURIComponent(threadId)}/messages`,
    { method: 'GET', signal },
  )
  if (
    !isRecord(response) ||
    !isUuid(response.thread_id) ||
    !Array.isArray(response.turns) ||
    !response.turns.every(isReplayTurn) ||
    typeof response.summarized !== 'boolean'
  ) {
    throw invalidThreadResponse('会话服务返回的历史记录格式不正确。')
  }
  return response as unknown as AgentThreadMessagesDto
}

/** 删除一个会话及其历史。删除不可撤销，调用方负责先向用户确认。 */
export async function deleteAgentThread(threadId: string): Promise<AgentThreadDeletionDto> {
  const response = await requestJson<unknown>(
    `/agent/threads/${encodeURIComponent(threadId)}`,
    { method: 'DELETE' },
  )
  if (!isRecord(response) || !isUuid(response.thread_id)) {
    throw invalidThreadResponse('会话服务返回的删除结果格式不正确。')
  }
  return response as unknown as AgentThreadDeletionDto
}

function isThreadSummary(value: unknown): value is AgentThreadSummaryDto {
  return (
    isRecord(value) &&
    isUuid(value.thread_id) &&
    typeof value.title === 'string' &&
    isIsoDateTime(value.created_at) &&
    isIsoDateTime(value.last_active_at)
  )
}

function isReplayTurn(value: unknown): value is AgentReplayTurnDto {
  return (
    isRecord(value) &&
    typeof value.question === 'string' &&
    // answer 允许空串：首轮就失败的会话存下来只有提问，没有回答。用 hasText 会把这种
    // 合法历史判成格式错误，界面上表现为「打不开自己的会话」。
    typeof value.answer === 'string' &&
    (value.traces === undefined ||
      (Array.isArray(value.traces) && value.traces.every(isReplayTrace)))
  )
}

function isReplayTrace(value: unknown): value is AgentReplayTraceDto {
  return (
    isRecord(value) &&
    hasText(value.tool) &&
    // content 为 null 表示历史里只有调用没有结果，是合法状态。
    (value.content === null || value.content === undefined || typeof value.content === 'string')
  )
}

function isIsoDateTime(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && !Number.isNaN(Date.parse(value))
}

function invalidThreadResponse(message: string): ApiError {
  return new ApiError({ message, code: 'response_invalid' })
}
