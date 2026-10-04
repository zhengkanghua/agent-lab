import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isNonNegativeInteger, isRecord, isUuid } from './json-guards'
import { isResolvedScope, isSelection, type KnowledgeBaseSelection } from './knowledge-scope'
import { isCitationList, isInvalidCitationList } from './agent-evidence'
import { isResolvedLlmModel } from './llm-models'

export type AgentThreadSummaryDto = components['schemas']['AgentThreadSummary']
export type AgentThreadListDto = components['schemas']['AgentThreadListResponse']
export type AgentThreadMessagesDto = components['schemas']['AgentThreadMessagesResponse']
export type AgentReplayTurnDto = components['schemas']['AgentReplayTurn']
export type AgentReplayTraceDto = components['schemas']['AgentReplayTrace']
export type AgentThreadDeletionDto = components['schemas']['AgentThreadDeletionResponse']
export type AgentThreadModelDto = components['schemas']['AgentThreadModel']

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
    response.thread_id.toLowerCase() !== threadId.toLowerCase() ||
    !isSelection(response.scope) ||
    !Array.isArray(response.turns) ||
    !response.turns.every(isReplayTurn) ||
    // 会话当前的选择：可能是 null（没选过，提问时用默认模型），但不接受别的类型的值。
    // 展示名允许为 null：目录里已经查不到那一条时服务端只回得出 id。
    (response.llm_model != null && !isThreadModel(response.llm_model)) ||
    // 在途运行的 id：可能是 null（没有在跑），但不接受别的类型的值。
    (response.active_run_id != null && !isUuid(response.active_run_id))
  ) {
    throw invalidThreadResponse('会话服务返回的历史记录格式不正确。')
  }
  return response as unknown as AgentThreadMessagesDto
}

export async function updateAgentThreadScope(
  threadId: string,
  scope: KnowledgeBaseSelection,
): Promise<void> {
  const value = await requestJson<unknown>(`/agent/threads/${encodeURIComponent(threadId)}/scope`, {
    method: 'PATCH',
    body: JSON.stringify(scope),
  })
  if (!isSelection(value))
    throw invalidThreadResponse('会话知识库选择保存结果无法确认，请重新打开会话核对。')
}

/**
 * 保存会话的模型选择。
 *
 * **服务端不判这个模型当前可不可用**（可用性只在提问开始之前那道门上判），所以一个已经失效的
 * 选择在这里同样返回成功；界面据此如实显示失效态，而不是静默换一个模型。
 */
export async function updateAgentThreadModel(
  threadId: string,
  llmModelId: string,
): Promise<void> {
  const value = await requestJson<unknown>(
    `/agent/threads/${encodeURIComponent(threadId)}/model`,
    { method: 'PATCH', body: JSON.stringify({ llm_model_id: llmModelId }) },
  )
  if (!isRecord(value) || !isUuid(value.llm_model_id))
    throw invalidThreadResponse('会话模型选择保存结果无法确认，请重新打开会话核对。')
}

/** 删除一个会话及其历史。删除不可撤销，调用方负责先向用户确认。 */
export async function deleteAgentThread(threadId: string): Promise<AgentThreadDeletionDto> {
  const response = await requestJson<unknown>(`/agent/threads/${encodeURIComponent(threadId)}`, {
    method: 'DELETE',
  })
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
    (value.status === 'completed' || value.status === 'incomplete') &&
    (value.scope == null || isResolvedScope(value.scope)) &&
    // 这一轮实际用的模型：旧轮次可能没有这一项（改动之前建立的会话），有就必须完整。
    (value.llm_model == null || isResolvedLlmModel(value.llm_model)) &&
    (value.run_id == null || isUuid(value.run_id)) &&
    (value.citations === undefined || isCitationList(value.citations)) &&
    (value.invalid_citations === undefined || isInvalidCitationList(value.invalid_citations)) &&
    (value.traces === undefined ||
      (Array.isArray(value.traces) && value.traces.every(isReplayTrace)))
  )
}

/** 会话当前保存的模型选择：id 必须有，展示名可以是 null（目录里查不到那一条）。 */
function isThreadModel(value: unknown): value is AgentThreadModelDto {
  return isRecord(value) && isUuid(value.id) && (value.display_name == null || hasText(value.display_name))
}

function isReplayTrace(value: unknown): value is AgentReplayTraceDto {
  return (
    isRecord(value) &&
    hasText(value.tool) &&
    (value.scope == null || isResolvedScope(value.scope)) &&
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
