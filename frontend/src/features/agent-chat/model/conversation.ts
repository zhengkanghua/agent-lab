import type { AgentToolCallEvent, AgentToolResultEvent } from '@/api/agent-chat'
import type { AgentErrorPresentation } from './agent-error'

/**
 * 一次工具调用在界面上的完整轨迹：从「模型决定要查」到「查到了什么」。
 *
 * `content` 为 null 表示还在执行中——工具调用事件先到，结果事件后到，中间那段时间要让
 * 用户看到「正在检索」而不是一片空白。
 */
export interface AgentToolTrace {
  /** v-for 的稳定 key。用递增序号而不是内容哈希：同一轮里可能有两次完全相同的调用。 */
  id: string
  /**
   * 后端给的调用 id，用来把结果配到这一条调用上。
   *
   * 和上面的 `id` 是两件事：`id` 是本地渲染 key，这个是后端契约里的值。回放出来的轨迹
   * 没有它（那边调用和结果已经合成一条了），所以可以为 null。
   */
  toolCallId: string | null
  tool: string
  arguments: Record<string, unknown>
  content: string | null
  failed: boolean
}

export type AgentTurnStatus = 'streaming' | 'done' | 'error' | 'cancelled'

/** 一问一答。提问是用户的原文，回答是逐 token 拼起来的增量。 */
export interface AgentTurn {
  id: string
  question: string
  answer: string
  traces: AgentToolTrace[]
  error: AgentErrorPresentation | null
  status: AgentTurnStatus
}

let sequence = 0

/** 生成界面内唯一的 id。只用于 v-for key，不参与任何持久化，所以不需要 UUID。 */
export function nextLocalId(prefix: string): string {
  sequence += 1
  return `${prefix}-${sequence}`
}

export function createTurn(question: string): AgentTurn {
  return {
    id: nextLocalId('turn'),
    question,
    answer: '',
    traces: [],
    error: null,
    status: 'streaming',
  }
}

export function appendToolCall(turn: AgentTurn, event: AgentToolCallEvent): void {
  turn.traces.push({
    id: nextLocalId('trace'),
    toolCallId: event.tool_call_id,
    tool: event.tool,
    arguments: event.arguments ?? {},
    content: null,
    failed: false,
  })
}

/**
 * 把工具结果并进对应的调用轨迹。
 *
 * 按 `tool_call_id` 精确配对，不依赖到达顺序。顺序靠不住：模型可以在一轮里用不同检索词
 * 并发调用同一个工具多次，两次调用的结果谁先返回没有保证。按工具名先来先配的话，那两条
 * 轨迹显示的检索词底下会挂上对方的结果。后端两个事件都带这个 id（见
 * `agent/streaming.py` 的 `_tool_events`），回放那条路一直是这么配的。
 *
 * 找不到对应调用时补一条只有结果的轨迹，而不是丢掉：宁可显示一条来源不明的工具结果，
 * 也不要让用户以为模型没查资料。
 */
export function applyToolResult(turn: AgentTurn, event: AgentToolResultEvent): void {
  const pending = turn.traces.find(
    (trace) => trace.toolCallId === event.tool_call_id && trace.content === null,
  )

  if (pending) {
    pending.content = event.content
    pending.failed = event.failed ?? false
    return
  }

  turn.traces.push({
    id: nextLocalId('trace'),
    toolCallId: event.tool_call_id,
    tool: event.tool,
    arguments: {},
    content: event.content,
    failed: event.failed ?? false,
  })
}

/**
 * 收尾时把还挂在「执行中」的轨迹标成结束。
 *
 * 运行被取消或中途报错时，工具结果事件永远不会到了。不收尾的话那几条轨迹会一直转圈，
 * 用户会以为还在跑。
 */
export function settlePendingTraces(turn: AgentTurn, note: string): void {
  for (const trace of turn.traces) {
    if (trace.content === null) {
      trace.content = note
      trace.failed = true
    }
  }
}

/**
 * 把回放接口返回的历史轮次转成界面用的轮次。
 *
 * 全部标成 `done`：它们是既成事实，没有「正在进行」的可能。历史里没有存下当时的失败原因，
 * 所以也不给 error——编一个出来会让用户以为那一轮报过某个具体错误。answer 为空串的轮次
 * 保持空串，由 `AgentTurnCard` 显示一句中性说明。
 *
 * 只有调用没有结果的工具轨迹（那一轮在工具返回前就断了）用 `pendingNote` 收尾，否则
 * `AgentToolTraceList` 会按 content 为 null 一直转圈，让人以为现在还在查。
 */
export function turnsFromReplay(
  replayTurns: readonly ReplayTurnInput[],
  pendingNote: string,
): AgentTurn[] {
  return replayTurns.map((replayTurn) => {
    const turn: AgentTurn = {
      id: nextLocalId('turn'),
      question: replayTurn.question,
      answer: replayTurn.answer,
      traces: (replayTurn.traces ?? []).map((trace) => ({
        id: nextLocalId('trace'),
        // 回放的轨迹里调用和结果已经合成一条，没有待配对的东西，所以不需要这个 id。
        toolCallId: null,
        tool: trace.tool,
        arguments: trace.arguments ?? {},
        content: trace.content ?? null,
        failed: trace.failed ?? false,
      })),
      error: null,
      status: 'done',
    }
    settlePendingTraces(turn, pendingNote)
    return turn
  })
}

/**
 * 回放输入的结构约束。
 *
 * 刻意只写用到的字段、不直接引用生成的 DTO 类型：这样这个纯函数不依赖 OpenAPI 生成物，
 * 测试可以用手写字面量调用它，而后端往响应里加字段也不会波及这里。
 */
export interface ReplayTurnInput {
  question: string
  answer: string
  traces?: readonly ReplayTraceInput[] | null
}

export interface ReplayTraceInput {
  tool: string
  arguments?: Record<string, unknown> | null
  content?: string | null
  failed?: boolean | null
}
