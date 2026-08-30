import { computed, onScopeDispose, ref, watch } from 'vue'
import { streamAgentChat, type AgentChatEvent } from '../../../api/agent-chat'
import { ApiError, isAbortError } from '../../../api/client'
import { presentAgentError } from '../model/agent-error'
import {
  MAX_MESSAGE_CHARACTERS,
  validateMessage,
  type AgentChatStatus,
} from '../model/agent-validation'
import {
  appendToolCall,
  applyToolResult,
  createTurn,
  settlePendingTraces,
  type AgentTurn,
} from '../model/conversation'

/** 注入流实现，测试里可以不打桩 fetch 就驱动整个状态机。 */
export type AgentChatStream = typeof streamAgentChat

const CANCELLED_TRACE_NOTE = '本轮对话已取消，这次工具调用的结果未送达。'
const FAILED_TRACE_NOTE = '本轮对话中断，这次工具调用的结果未送达。'

/**
 * 编排一次多轮 Agent 对话。
 *
 * 与检索页的 `useSearchRequest` 不共用底座，是有意的：那个底座的核心是「一次请求换一批
 * 结果」，reset/clear 都会清空结果。对话正相反——新的一轮要保留之前所有轮次，而且流式
 * 过程中要持续改写最后一轮。硬套会让两边都变形。
 *
 * 陈旧响应守卫沿用同一招：requestSequence 与本轮 runId 比较。AbortController 只能拦住
 * 还没 resolve 的读取，而「事件已经拿到、await 还没恢复执行」的窗口内 abort 不起作用，
 * 只有序号比较能拦住已取消的那一轮继续往界面上写字。
 */
export function useAgentChat(stream: AgentChatStream = streamAgentChat) {
  const draft = ref('')
  // 用深层 ref 而不是检索页那样的 shallowRef：流式过程要原地改写最后一轮的 answer 和
  // traces，shallowRef 只跟踪整个数组的替换，逐 token 追加不会触发渲染。对话对象很小
  // （几轮 × 几百字），深层代理的开销远小于「每个 token 复制一遍整个数组」。
  const turns = ref<AgentTurn[]>([])
  const status = ref<AgentChatStatus>('idle')
  const inputError = ref<string | null>(null)
  const threadId = ref<string | null>(null)
  const systemPrompt = ref('')

  let runSequence = 0
  let activeController: AbortController | null = null

  const remainingCharacters = computed(() => MAX_MESSAGE_CHARACTERS - draft.value.length)
  const canSend = computed(() => draft.value.trim().length > 0 && status.value !== 'streaming')
  const isStreaming = computed(() => status.value === 'streaming')

  watch(draft, (value) => {
    if (inputError.value && !validateMessage(value)) {
      inputError.value = null
    }
  })

  async function send(): Promise<void> {
    inputError.value = validateMessage(draft.value)
    if (inputError.value || status.value === 'streaming') return

    const question = draft.value.trim()
    const runId = ++runSequence
    const controller = new AbortController()
    activeController = controller

    turns.value.push(createTurn(question))
    // 取回代理对象而不是用上面那个原始对象：深层 ref 里只有代理上的写入会触发渲染。
    const live = turns.value[turns.value.length - 1]!

    draft.value = ''
    status.value = 'streaming'

    try {
      for await (const event of stream({
        message: question,
        threadId: threadId.value,
        systemPrompt: systemPrompt.value,
        signal: controller.signal,
      })) {
        // 已被取消或已被更新的一轮不再往界面上写：break 会走生成器的 finally，
        // 顺带取消 reader、关掉连接。
        if (runId !== runSequence) break
        applyEvent(live, event)
      }

      if (runId !== runSequence) return

      if (live.status === 'streaming') {
        // 流正常结束但没有 done 事件（例如服务端直接断开）。当成完成处理，已收到的
        // 回答仍然留在界面上——它是真的模型输出，丢掉比留着更糟。
        live.status = 'done'
        settlePendingTraces(live, FAILED_TRACE_NOTE)
      }
    } catch (error) {
      if (runId !== runSequence) return

      if (isAbortError(error)) {
        live.status = 'cancelled'
        settlePendingTraces(live, CANCELLED_TRACE_NOTE)
        return
      }

      live.status = 'error'
      live.error = presentAgentError(
        error instanceof ApiError
          ? error
          : new ApiError({
              message: 'Unexpected agent failure.',
              code: 'unknown_error',
              cause: error,
            }),
      )
      settlePendingTraces(live, FAILED_TRACE_NOTE)
    } finally {
      if (runId === runSequence) {
        activeController = null
        status.value = 'idle'
      }
    }
  }

  function applyEvent(turn: AgentTurn, event: AgentChatEvent): void {
    switch (event.event) {
      case 'token':
        turn.answer += event.text
        break
      case 'tool_call':
        appendToolCall(turn, event)
        break
      case 'tool_result':
        applyToolResult(turn, event)
        break
      case 'done':
        // 服务端在新建会话时才生成新 id，续聊时回的是同一个，直接覆盖即可。
        threadId.value = event.thread_id
        turn.status = 'done'
        settlePendingTraces(turn, FAILED_TRACE_NOTE)
        break
      case 'error':
        turn.status = 'error'
        turn.error = presentAgentError(
          new ApiError({
            message: event.detail,
            code: event.code,
            retryable: event.retryable,
          }),
        )
        settlePendingTraces(turn, FAILED_TRACE_NOTE)
        break
    }
  }

  /** 取消在途的这一轮。已经收到的回答保留，状态标成 cancelled。 */
  function cancel(): void {
    if (status.value !== 'streaming') return
    cancelActiveRun()
    const last = turns.value[turns.value.length - 1]
    if (last && last.status === 'streaming') {
      last.status = 'cancelled'
      settlePendingTraces(last, CANCELLED_TRACE_NOTE)
    }
    status.value = 'idle'
  }

  /** 重发最后一轮的提问。失败轮保留在历史里，便于对照前后两次回答。 */
  function retry(): Promise<void> {
    const last = turns.value[turns.value.length - 1]
    if (!last || status.value === 'streaming') return Promise.resolve()
    draft.value = last.question
    return send()
  }

  /**
   * 开一个新会话。
   *
   * 必须同时丢掉 threadId：只清界面不换 id 的话，下一轮仍会带上旧 thread，模型看得见
   * 用户以为已经删掉的历史。
   */
  function startNewConversation(): void {
    cancelActiveRun()
    turns.value = []
    draft.value = ''
    threadId.value = null
    inputError.value = null
    status.value = 'idle'
  }

  function cancelActiveRun(): void {
    runSequence += 1
    activeController?.abort()
    activeController = null
  }

  onScopeDispose(cancelActiveRun)

  return {
    draft,
    turns,
    status,
    inputError,
    threadId,
    systemPrompt,
    remainingCharacters,
    canSend,
    isStreaming,
    send,
    cancel,
    retry,
    startNewConversation,
  }
}
