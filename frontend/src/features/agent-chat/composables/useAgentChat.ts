import { computed, onScopeDispose, ref, watch } from 'vue'
import { streamAgentChat, type AgentChatEvent } from '@/api/agent-chat'
import { getAgentThreadMessages } from '@/api/agent-threads'
import { ApiError, isAbortError } from '@/api/client'
import { presentAgentError, type AgentErrorPresentation } from '../model/agent-error'
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
  turnsFromReplay,
  type AgentTurn,
} from '../model/conversation'

/** 注入流实现，测试里可以不打桩 fetch 就驱动整个状态机。 */
export type AgentChatStream = typeof streamAgentChat

/** 注入回放实现，同上。 */
export type AgentThreadLoader = typeof getAgentThreadMessages

const CANCELLED_TRACE_NOTE = '本轮对话已取消，这次工具调用的结果未送达。'
const FAILED_TRACE_NOTE = '本轮对话中断，这次工具调用的结果未送达。'
// 回放专用：历史里那次调用没有结果，是当时就断了，不是现在还在查。
const HISTORY_TRACE_NOTE = '这次工具调用没有结果记录，当时的对话中断了。'

/**
 * 编排一次多轮 Agent 对话。
 *
 * 检索页重构后也有 `useSearchStream`，同样做多轮累积——但两边仍是两套实现，刻意不共用：
 * 检索流的每一轮是独立、离散的一次搜索，没有跨轮状态；Agent 每一轮则要持续改写最后一轮
 * 的流式回答、还要按模型/tool 轨迹重建，且历史来自服务端回放。硬套会让两边都变形。
 *
 * 陈旧响应守卫沿用同一招：requestSequence 与本轮 runId 比较。AbortController 只能拦住
 * 还没 resolve 的读取，而「事件已经拿到、await 还没恢复执行」的窗口内 abort 不起作用，
 * 只有序号比较能拦住已取消的那一轮继续往界面上写字。
 */
export function useAgentChat(
  stream: AgentChatStream = streamAgentChat,
  loadThreadMessages: AgentThreadLoader = getAgentThreadMessages,
) {
  const draft = ref('')
  // 用深层 ref 而不是检索页那样的 shallowRef：流式过程要原地改写最后一轮的 answer 和
  // traces，shallowRef 只跟踪整个数组的替换，逐 token 追加不会触发渲染。对话对象很小
  // （几轮 × 几百字），深层代理的开销远小于「每个 token 复制一遍整个数组」。
  const turns = ref<AgentTurn[]>([])
  const status = ref<AgentChatStatus>('idle')
  const inputError = ref<string | null>(null)
  const threadId = ref<string | null>(null)
  const systemPrompt = ref('')
  // 回放状态与流式状态分开：一个是「历史读出来了吗」，一个是「这一轮在生成吗」。合成一个
  // status 会让「正在读历史」误触发输入框禁用之外的流式 UI（停止按钮、光标）。
  const isLoadingThread = ref(false)
  const threadError = ref<AgentErrorPresentation | null>(null)
  // 早期历史被压缩掉时为真。界面必须如实说明，不能让人以为看到的就是全部。
  const isHistoryTruncated = ref(false)

  let runSequence = 0
  let loadSequence = 0
  let activeController: AbortController | null = null
  let activeLoadController: AbortController | null = null

  const remainingCharacters = computed(() => MAX_MESSAGE_CHARACTERS - draft.value.length)
  // 读历史期间也不许发送：那时 threadId 还没设上，发出去会被当成新会话，用户以为自己在
  // 续聊、实际上开了一个新的，而且旧会话的历史马上会覆盖掉界面。
  const canSend = computed(
    () => draft.value.trim().length > 0 && status.value !== 'streaming' && !isLoadingThread.value,
  )
  const isStreaming = computed(() => status.value === 'streaming')

  watch(draft, (value) => {
    if (inputError.value && !validateMessage(value)) {
      inputError.value = null
    }
  })

  async function send(): Promise<void> {
    inputError.value = validateMessage(draft.value)
    if (inputError.value || status.value === 'streaming' || isLoadingThread.value) return

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
        // 和 done 一样认下这个 id：归属行在流开始之前就写好了，失败的这一轮同样属于一个
        // 已存在的会话。不认的话「重发这一轮」会不带 thread_id 发出去，服务端当成新会话，
        // 列表里于是多一条只有提问的记录。
        threadId.value = event.thread_id
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
   * 载入一个既有会话的历史，载完就可以接着聊。
   *
   * 成功时 `threadId` 指向它，之后 `send()` 会带上这个 id 续聊。失败时**不设** `threadId`：
   * 设了的话用户在一个打不开的会话里发问，那条提问会被后端按归属拒掉，界面上却像是模型出错。
   *
   * 陈旧响应守卫与 `send()` 同理——连点两个会话时，先发的请求可能后到。只比较序号，不依赖
   * abort：abort 拦不住「响应已拿到、await 还没恢复」那个窗口。
   */
  async function loadThread(targetThreadId: string): Promise<void> {
    // 切会话等于放弃在途的那一轮。不取消的话，旧会话的 token 会继续写进新会话的界面。
    cancelActiveRun()
    cancelActiveLoad()

    const loadId = ++loadSequence
    const controller = new AbortController()
    activeLoadController = controller

    turns.value = []
    draft.value = ''
    inputError.value = null
    threadError.value = null
    isHistoryTruncated.value = false
    status.value = 'idle'
    isLoadingThread.value = true

    try {
      const replay = await loadThreadMessages(targetThreadId, controller.signal)
      if (loadId !== loadSequence) return

      turns.value = turnsFromReplay(replay.turns ?? [], HISTORY_TRACE_NOTE)
      isHistoryTruncated.value = replay.summarized
      threadId.value = targetThreadId
    } catch (error) {
      if (loadId !== loadSequence || isAbortError(error)) return

      // 打不开就退回「没有会话」的状态：threadId 留空，用户可以直接开始一段新对话。
      threadId.value = null
      threadError.value = presentAgentError(
        error instanceof ApiError
          ? error
          : new ApiError({
              message: 'Unexpected thread load failure.',
              code: 'unknown_error',
              cause: error,
            }),
      )
    } finally {
      if (loadId === loadSequence) {
        activeLoadController = null
        isLoadingThread.value = false
      }
    }
  }

  /**
   * 开一个新会话。
   *
   * 必须同时丢掉 threadId：只清界面不换 id 的话，下一轮仍会带上旧 thread，模型看得见
   * 用户以为已经删掉的历史。
   */
  function startNewConversation(): void {
    cancelActiveRun()
    cancelActiveLoad()
    turns.value = []
    draft.value = ''
    threadId.value = null
    inputError.value = null
    threadError.value = null
    isHistoryTruncated.value = false
    isLoadingThread.value = false
    status.value = 'idle'
  }

  function cancelActiveLoad(): void {
    loadSequence += 1
    activeLoadController?.abort()
    activeLoadController = null
  }

  function cancelActiveRun(): void {
    runSequence += 1
    activeController?.abort()
    activeController = null
  }

  onScopeDispose(() => {
    cancelActiveRun()
    cancelActiveLoad()
  })

  return {
    draft,
    turns,
    status,
    inputError,
    threadId,
    systemPrompt,
    isLoadingThread,
    threadError,
    isHistoryTruncated,
    remainingCharacters,
    canSend,
    isStreaming,
    send,
    cancel,
    retry,
    loadThread,
    startNewConversation,
  }
}
