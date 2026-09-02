import type { components } from './generated/openapi'
import {
  ApiError,
  isAbortError,
  readJsonBody,
  requestJson,
  resolveApiUrl,
  toApiError,
} from './client'
import { hasText, isRecord, isUuid } from './json-guards'

export type AgentChatRequest = components['schemas']['AgentChatRequest']
export type AgentChatEvent = components['schemas']['AgentChatEventEnvelope']
export type AgentTokenEvent = components['schemas']['AgentTokenEvent']
export type AgentToolCallEvent = components['schemas']['AgentToolCallEvent']
export type AgentToolResultEvent = components['schemas']['AgentToolResultEvent']
export type AgentDoneEvent = components['schemas']['AgentDoneEvent']
export type AgentErrorEvent = components['schemas']['AgentErrorEvent']
export type AgentDefaultPromptResponse = components['schemas']['AgentDefaultPromptResponse']

/**
 * 两帧之间允许的最长静默。
 *
 * 不能复用 client.ts 的 API_REQUEST_TIMEOUT_MS：那是「整个请求必须在 45 秒内结束」，
 * 而一次 Agent 运行可能要几分钟，用它会在模型还在写的时候掐断。这里改成「空闲超时」——
 * 后端每 15 秒发一次心跳注释帧（见 agent/limits.py 的 SSE_HEARTBEAT_INTERVAL_SECONDS），
 * 所以连续 60 秒收不到任何字节意味着链路真的断了，而不是模型在思考。
 *
 * 取心跳间隔的 4 倍而不是 2 倍：中间任何一跳的缓冲都可能让心跳晚到，留出容错余量比
 * 误判断线更重要——误判会丢掉一次已经付过钱的模型调用。
 */
export const AGENT_STREAM_IDLE_TIMEOUT_MS = 60_000

/** 建立连接（拿到响应头）的超时。流开始之后就换成上面的空闲超时。 */
export const AGENT_STREAM_CONNECT_TIMEOUT_MS = 30_000

const SSE_FRAME_SEPARATOR = '\n\n'
const SSE_DATA_FIELD = 'data:'

export interface StreamAgentChatOptions {
  message: string
  /** 续聊时带上上一轮 done 事件给的会话 id；省略表示新建会话。 */
  threadId?: string | null
  /** 覆盖本次运行的系统提示词；省略或空白表示用服务端默认的那份。 */
  systemPrompt?: string | null
  signal?: AbortSignal
}

/**
 * 发起一次 Agent 对话，按到达顺序逐个产出 SSE 事件。
 *
 * **为什么用 fetch + getReader 而不是 EventSource**：EventSource 只能发 GET，没法带请求体，
 * 而提问和自定义提示词都必须走 body（放进 query string 会被网关日志和浏览器历史记录下来）；
 * 它也不支持自定义请求头，`credentials: 'same-origin'` 这类控制同样拿不到。
 *
 * 契约校验放在这一层：每帧都过一遍形状检查，不符合就抛 `response_invalid` 结束流，和
 * document-search.ts 拒绝结果漂移是同一个取舍——宁可明确失败，也不把半个未知结构交给渲染层。
 *
 * 注意 `error` 事件不是异常：它是后端已分类的失败，作为正常事件产出（流一旦开始就没法再改
 * HTTP 状态码了）。调用方要自己判断最后一个事件是 `done` 还是 `error`。
 */
export async function* streamAgentChat({
  message,
  threadId,
  systemPrompt,
  signal,
}: StreamAgentChatOptions): AsyncGenerator<AgentChatEvent, void, void> {
  const payload: AgentChatRequest = { message }
  if (threadId) payload.thread_id = threadId
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt

  // 内部 controller 同时承载三个中止来源：调用方的 signal、连接超时、空闲超时。
  // 只有它能中止 fetch，所以调用方的 signal 要转发进来。
  const controller = new AbortController()
  let timedOutAt: 'connect' | 'idle' | null = null
  let timeoutId: ReturnType<typeof setTimeout> | undefined

  const abortFromCaller = () => controller.abort()
  const armTimeout = (ms: number, reason: 'connect' | 'idle') => {
    clearTimeout(timeoutId)
    timeoutId = setTimeout(() => {
      timedOutAt = reason
      controller.abort()
    }, ms)
  }

  if (signal?.aborted) {
    abortFromCaller()
  } else {
    signal?.addEventListener('abort', abortFromCaller, { once: true })
  }

  try {
    let response: Response
    armTimeout(AGENT_STREAM_CONNECT_TIMEOUT_MS, 'connect')

    try {
      response = await fetch(resolveApiUrl('/agent/chat'), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })
    } catch (error) {
      throw translateStreamFailure(error, timedOutAt, signal)
    }

    if (!response.ok) {
      // 流还没开始，失败仍然带得动 HTTP 状态码，所以走和 JSON 接口同一套错误契约。
      throw toApiError(response, await readJsonBody(response))
    }

    if (response.body === null) {
      throw new ApiError({
        message: 'The agent service returned a response without a body.',
        status: response.status,
        code: 'response_invalid',
      })
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        armTimeout(AGENT_STREAM_IDLE_TIMEOUT_MS, 'idle')

        let chunk: ReadableStreamReadResult<Uint8Array>
        try {
          chunk = await reader.read()
        } catch (error) {
          throw translateStreamFailure(error, timedOutAt, signal)
        }

        if (chunk.done) {
          // 服务端正常关闭连接。缓冲区里可能还剩最后一帧没有收尾空行的残余，按帧解析掉。
          buffer += decoder.decode()
          const trailing = parseFrame(buffer)
          if (trailing !== null) yield trailing
          return
        }

        // stream: true 让跨 chunk 切开的多字节 UTF-8 字符不被解成替换字符——中文一个字
        // 三个字节，正好会被 TCP 分片切断。
        buffer += decoder.decode(chunk.value, { stream: true })

        // 只把 CRLF 规范成 LF：SSE 规范允许 CRLF 分帧，而后端发的是 LF。整体替换而不是
        // 只处理帧尾，是为了避免 \r 落在两个 chunk 边界上时漏判。
        buffer = buffer.replace(/\r\n/g, '\n')

        let separatorIndex = buffer.indexOf(SSE_FRAME_SEPARATOR)
        while (separatorIndex !== -1) {
          const frame = buffer.slice(0, separatorIndex)
          buffer = buffer.slice(separatorIndex + SSE_FRAME_SEPARATOR.length)
          const event = parseFrame(frame)
          if (event !== null) yield event
          separatorIndex = buffer.indexOf(SSE_FRAME_SEPARATOR)
        }
      }
    } finally {
      // 调用方提前 break 出 for await 时，生成器的 finally 会跑到这里。取消 reader 才会
      // 真正关闭底层连接，否则后端会继续算这次运行的钱。
      await reader.cancel().catch(() => undefined)
    }
  } finally {
    clearTimeout(timeoutId)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

/** 取默认系统提示词，用于把自定义提示词输入框预填成可编辑的起点。 */
export async function fetchAgentDefaultPrompt(signal?: AbortSignal): Promise<string> {
  const response = await requestJson<unknown>('/agent/default-prompt', { method: 'GET', signal })

  if (!isRecord(response) || !hasText(response.system_prompt)) {
    throw new ApiError({
      message: 'The agent service returned an unexpected default prompt shape.',
      code: 'response_invalid',
    })
  }

  return response.system_prompt
}

/**
 * 把一帧 SSE 文本解析成事件；心跳注释、空帧和不带 data 字段的帧返回 null。
 *
 * 心跳帧（`: keep-alive`）天然被这里过掉：它以冒号开头，不以 `data:` 开头。
 * 未知字段（`event:`、`id:`、`retry:`）同样忽略——后端的事件类型写在 JSON 的 `event`
 * 属性里，不用 SSE 的 event 字段，所以这里只需要 data。
 */
function parseFrame(frame: string): AgentChatEvent | null {
  const dataLines = frame
    .split('\n')
    .filter((line) => line.startsWith(SSE_DATA_FIELD))
    .map((line) => line.slice(SSE_DATA_FIELD.length).replace(/^ /, ''))

  if (dataLines.length === 0) return null

  // 多条 data 行按规范用换行拼接。后端目前一帧只发一条，但拼接是规范行为，照做不吃亏。
  const payload = dataLines.join('\n')
  if (!payload.trim()) return null

  let parsed: unknown
  try {
    parsed = JSON.parse(payload) as unknown
  } catch (error) {
    throw new ApiError({
      message: 'The agent service sent an unreadable event.',
      code: 'response_invalid',
      cause: error,
    })
  }

  if (!isAgentChatEvent(parsed)) {
    throw new ApiError({
      message: 'The agent service sent an event that does not match the contract.',
      code: 'response_invalid',
    })
  }

  return parsed
}

function isAgentChatEvent(value: unknown): value is AgentChatEvent {
  if (!isRecord(value)) return false

  switch (value.event) {
    case 'token':
      return typeof value.text === 'string'
    case 'tool_call':
      // tool_call_id 是必需的：少了它，工具结果就只能按名字先来先配，同一个工具在一轮里
      // 并发调用多次时会把两条轨迹的参数和结果对调。
      return (
        hasText(value.tool_call_id) &&
        hasText(value.tool) &&
        (value.arguments === undefined || isRecord(value.arguments))
      )
    case 'tool_result':
      return (
        hasText(value.tool_call_id) &&
        hasText(value.tool) &&
        typeof value.content === 'string' &&
        (value.failed === undefined || typeof value.failed === 'boolean')
      )
    case 'done':
      return isUuid(value.thread_id)
    case 'error':
      // thread_id 和 done 一样是必需的：失败的那一轮也已经有会话行，前端要靠它把重试
      // 发回同一个会话。
      return (
        isUuid(value.thread_id) &&
        hasText(value.code) &&
        typeof value.detail === 'string' &&
        typeof value.retryable === 'boolean'
      )
    default:
      return false
  }
}

/**
 * 把 fetch/read 抛出的中止或网络异常翻译成 ApiError，或原样抛出调用方自己的取消。
 *
 * 判定顺序要紧：先看是不是调用方主动取消（那不是错误，原样抛给上层的 isAbortError 分支
 * 忽略掉），再看是不是我们自己的超时，最后才归为网络故障。
 */
function translateStreamFailure(
  error: unknown,
  timedOutAt: 'connect' | 'idle' | null,
  callerSignal: AbortSignal | undefined,
): unknown {
  if (callerSignal?.aborted) return error

  if (timedOutAt !== null) {
    return new ApiError({
      message:
        timedOutAt === 'connect'
          ? 'The agent service did not respond in time.'
          : 'The agent stream went silent.',
      code: 'request_timeout',
      retryable: true,
      cause: error,
    })
  }

  if (isAbortError(error)) return error

  return new ApiError({
    message: 'Unable to reach the agent service.',
    code: 'network_error',
    retryable: true,
    cause: error,
  })
}
