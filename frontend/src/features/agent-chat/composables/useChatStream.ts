import { computed } from 'vue'
import { stopAgentRun, streamAgentChat, type AgentChatEvent } from '@/api/agent-chat'
import { copySelection } from '@/api/knowledge-scope'
import { ApiError } from '@/api/client'
import { presentAgentError } from '../model/agent-error'
import { MAX_MESSAGE_CHARACTERS, validateMessage } from '../model/agent-validation'
import {
  appendToolCall,
  applyToolResult,
  createTurn,
  settlePendingTraces,
  type AgentTurn,
} from '../model/conversation'
import type { ChatState } from './chat-state'
import type { useChatModel } from './useChatModel'
import type { useChatScope } from './useChatScope'
import type { useThreadHistory } from './useThreadHistory'

/* 一次运行的生命周期：发起、读事件、停止、取消、重发。
 *
 * 从 useAgentChat 里分出来的一块。它替换掉的做法是「用一个 635 行的文件同时管七件事」；
 * 这里只管流本身，历史对账、范围保存与模型保存通过参数注入（同步历史、读在途 run id、
 * 会话刚建出来时补存改选），依赖方向是单向的：流 → 历史，流 → 范围/模型，没有回头边。
 *
 * 陈旧响应守卫沿用同一招：runSequence 与本轮 runId 比较。AbortController 只能拦住还没 resolve
 * 的读取，而「事件已经拿到、await 还没恢复执行」的窗口内 abort 不起作用，只有序号比较能拦住
 * 已取消的那一轮继续往界面上写字。
 */

/** 注入流实现，测试里可以不打桩 fetch 就驱动整个状态机。 */
export type AgentChatStream = typeof streamAgentChat

/** 注入停止实现，测试里可以点「停止」这条路。 */
export type AgentRunStopper = typeof stopAgentRun

const FAILED_TRACE_NOTE = '本轮对话中断，这次工具调用的结果未送达。'

type ScopeApi = ReturnType<typeof useChatScope>
type ModelApi = ReturnType<typeof useChatModel>
type HistoryApi = ReturnType<typeof useThreadHistory>

export interface UseChatStreamOptions {
  stream?: AgentChatStream
  stopRun?: AgentRunStopper
  getScopeError?: () => string | null
  onThreadCreated?: (threadId: string) => void
}

export function useChatStream(
  state: ChatState,
  scope: ScopeApi,
  model: ModelApi,
  history: HistoryApi,
  {
    stream = streamAgentChat,
    stopRun = stopAgentRun,
    getScopeError = () => null,
    onThreadCreated,
  }: UseChatStreamOptions = {},
) {
  const remainingCharacters = computed(() => MAX_MESSAGE_CHARACTERS - state.draft.value.length)
  const isStreaming = computed(() => state.status.value === 'streaming')

  let runSequence = 0
  let activeController: AbortController | null = null
  // 用户已点停止、但还没拿到运行 id。``run_started`` 是第一个事件，这个窗口极小，
  // 但留一个意图位比让那次点击静默丢掉要好。
  let stopPending = false

  /** 服务端手上确实有一次可以停的运行：流式连接在看它，或界面处在「等待中」。 */
  const canStop = computed(() => isStreaming.value || history.awaitingRunId.value !== null)

  /**
   * 读历史期间也不许发送：那时 threadId 还没设上，发出去会被当成新会话，用户以为自己在
   * 续聊、实际上开了一个新的，而且旧会话的历史马上会覆盖掉界面。
   */
  const canSend = computed(
    () =>
      state.draft.value.trim().length > 0 &&
      state.status.value !== 'streaming' &&
      !history.isLoadingThread.value &&
      !scope.savingScope.value &&
      // 服务端还在跑这一轮时不能发：发出去会被它以 409 拒掉，而界面上看起来就像模型出错。
      history.awaitingRunId.value === null &&
      !getScopeError(),
  )

  async function send(): Promise<void> {
    state.inputError.value = validateMessage(state.draft.value) || getScopeError()
    if (
      state.inputError.value ||
      state.status.value === 'streaming' ||
      history.isLoadingThread.value ||
      scope.savingScope.value ||
      // 服务端还在跑这一轮时不能发（见 canSend 的说明）。retry() 也会走到这里，所以这一道
      // 不能只写在 canSend 里。
      history.awaitingRunId.value !== null
    )
      return

    const question = state.draft.value.trim()
    const submittedSelection = copySelection(scope.selection.value)
    const submittedScopeVersion = scope.editVersion()
    // 这一轮别把「另一个模型」带成上一轮那个：模型也一样在出发前拍一份。
    const submittedModelId = model.selectedId()
    const submittedModelVersion = model.editVersion()
    const runId = ++runSequence
    const controller = new AbortController()
    activeController = controller

    state.turns.value.push(createTurn(question))
    // 取回代理对象而不是用上面那个原始对象：深层 ref 里只有代理上的写入会触发渲染。
    const live = state.turns.value[state.turns.value.length - 1]!

    state.draft.value = ''
    history.threadError.value = null
    state.status.value = 'streaming'

    try {
      for await (const event of stream({
        message: question,
        threadId: state.threadId.value,
        scope: submittedSelection,
        llmModelId: submittedModelId,
        signal: controller.signal,
      })) {
        // 已被取消或已被更新的一轮不再往界面上写：break 会走生成器的 finally，
        // 顺带取消 reader、关掉连接。
        if (runId !== runSequence) break
        applyEvent(live, event)
        if (event.event === 'run_started' && submittedScopeVersion !== scope.editVersion()) {
          // 新会话还没拿到 ID 时发生的改选，也要保存为下一次提问的选择。
          void scope.persistSelection(
            event.thread_id,
            copySelection(scope.selection.value),
            scope.editVersion(),
          )
        }
        // 模型那一份同理：会话刚建出来时那次改选不能丢。
        if (event.event === 'run_started' && submittedModelVersion !== model.editVersion()) {
          const current = model.selectedId()
          if (current !== null)
            void model.persistSelection(event.thread_id, current, model.editVersion())
        }
        if (event.event === 'done' || event.event === 'error') break
      }

      if (runId !== runSequence) return

      if (live.status === 'streaming') {
        // 已收到的片段保留，但没有服务端终态就不能当作完整答案。
        live.status = 'incomplete'
        settlePendingTraces(live, FAILED_TRACE_NOTE)
      } else if (live.status === 'done' || live.status === 'incomplete') {
        await history.synchronizeHistory({
          signal: controller.signal,
          isStale: () => runId !== runSequence,
        })
      }
    } catch (error) {
      if (runId !== runSequence) return

      // 连接被中断不一定是失败：部署切换会断掉订阅连接，而运行本身还在服务端跑。
      // 先读一次回放，服务端仍报有在途运行就转成等待态，而不是把这轮标成错误。
      if (await history.recoverInterruptedRun(() => runId !== runSequence)) return

      live.status = 'error'
      live.error = presentAgentError(
        error instanceof ApiError
          ? error
          : new ApiError({
              message: 'Agent 对话出现了未预期的失败。',
              code: 'unknown_error',
              cause: error,
            }),
      )
      settlePendingTraces(live, FAILED_TRACE_NOTE)
    } finally {
      if (runId === runSequence) {
        activeController = null
        state.status.value = 'idle'
      }
    }
  }

  function acceptThreadId(id: string | null): void {
    const created = state.threadId.value === null && id !== null
    state.threadId.value = id
    if (created && id !== null) onThreadCreated?.(id)
  }

  function applyEvent(turn: AgentTurn, event: AgentChatEvent): void {
    switch (event.event) {
      case 'run_started':
        acceptThreadId(event.thread_id)
        turn.runId = event.run_id
        turn.scope = event.scope
        scope.scopeSaveError.value = null
        // 点停止早于首帧时，现在才拿得到运行 id。
        if (stopPending) requestStop(event.thread_id, event.run_id)
        break
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
        acceptThreadId(event.thread_id)
        turn.answer = event.answer
        turn.status = event.status === 'completed' ? 'done' : 'incomplete'
        turn.citations = event.citations ?? []
        turn.invalidCitations = event.invalid_citations ?? []
        settlePendingTraces(turn, FAILED_TRACE_NOTE)
        break
      case 'error':
        // 和 done 一样认下这个 id：归属行在流开始之前就写好了，失败的这一轮同样属于一个
        // 已存在的会话。不认的话「重发这一轮」会不带 thread_id 发出去，服务端当成新会话，
        // 列表里于是多一条只有提问的记录。
        acceptThreadId(event.thread_id)
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

  /**
   * 停下来这一轮：请求服务端真的停下它，并**保持连接等它的终态事件**。
   *
   * 刻意不在本地造结局。运行已经不在连接上，本地中止只会让界面白白做出一副「停了」的样子，
   * 而服务端继续跑完并计费；所以这里只发一个停止请求，之后照旧读事件直到服务端给出 ``done``。
   * 于是「点停止时看到的」与「刷新后看到的」是同一份口径（两边都取自服务端的终态与回放）。
   *
   * 重复点击无害：服务端只对「在途运行的 id 与请求里的相等」才写停止标志，不等一律幂等成功。
   */
  function cancel(): void {
    if (state.status.value === 'streaming') {
      const last = state.turns.value[state.turns.value.length - 1]
      if (!state.threadId.value || !last?.runId) {
        // run_started 还没到：记下意图，首帧到了再补发。
        stopPending = true
        return
      }
      requestStop(state.threadId.value, last.runId)
      return
    }
    // 等待态（刷新或断开回退之后在等这一轮结束）：停止用回放给出的在途运行 id。
    // 没有在途 id 就不渲染停止键，所以这里不需要兜底分支。
    if (state.threadId.value && history.awaitingRunId.value !== null) {
      requestStop(state.threadId.value, history.awaitingRunId.value)
    }
  }

  /** 发一次停止请求。失败只记在意图位上，不弹错：用户再点一次就是了。 */
  function requestStop(targetThreadId: string, runId: string): void {
    stopPending = false
    void stopRun(targetThreadId, runId).catch(() => {
      // 不做别的。本地中止连接是假停止（服务端会继续跑完），所以不能拿它当兼容路径。
    })
  }

  /**
   * 放弃本地这条流：不再读它，但**不**请求服务端停止。
   *
   * 用在「用户离开这条连接」的场合（退出登录）。按 ADR 0035，离开只意味着少一个订阅者，
   * 那次运行照旧跑完并落进会话历史——用户下次打开这个会话能看到完整答案。要真的停下运行，
   * 用 ``cancel()``，两者的区别就是「我走了但活干完」与「不要了」。
   */
  function abandonRun(): void {
    cancelActiveRun()
    state.status.value = 'idle'
  }

  /** 重发最后一轮的提问。失败轮保留在历史里，便于对照前后两次回答。 */
  function retry(): Promise<void> {
    const last = state.turns.value[state.turns.value.length - 1]
    if (!last || state.status.value === 'streaming') return Promise.resolve()
    state.draft.value = last.question
    return send()
  }

  function cancelActiveRun(): void {
    runSequence += 1
    stopPending = false
    activeController?.abort()
    activeController = null
  }

  return {
    send,
    cancel,
    abandonRun,
    retry,
    cancelActiveRun,
    isStreaming,
    canSend,
    canStop,
    remainingCharacters,
  }
}
