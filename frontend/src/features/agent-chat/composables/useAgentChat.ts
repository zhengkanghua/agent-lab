import { computed, onScopeDispose, ref, watch } from 'vue'
import { stopAgentRun, streamAgentChat, type AgentChatEvent } from '@/api/agent-chat'
import {
  getAgentThreadMessages,
  updateAgentThreadScope,
  type AgentThreadMessagesDto,
} from '@/api/agent-threads'
import { copySelection, isSelection, type KnowledgeBaseSelection } from '@/api/knowledge-scope'
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

/** 注入停止实现，测试里可以不打桩 fetch 就驱动「点停止」这条路。 */
export type AgentRunStopper = typeof stopAgentRun

export interface UseAgentChatOptions {
  stream?: AgentChatStream
  loadThreadMessages?: AgentThreadLoader
  stopRun?: AgentRunStopper
  /** 轮询「在途运行结束了吗」的间隔。只为让测试不必真的等三秒，生产不要传。 */
  runWatchIntervalMs?: number
  /** 断流后读回放的尝试次数；只为让测试不必真的等三十秒，生产不要传。 */
  runRecoverAttempts?: number
  /** 两次读回放之间的间隔；同上。 */
  runRecoverIntervalMs?: number
  getScopeError?: () => string | null
  saveScope?: typeof updateAgentThreadScope
  onThreadCreated?: (threadId: string) => void
}

/**
 * 刷新/切回来时，服务端还报「有运行在途」的话，多久重读一次会话历史。
 *
 * 只用来知道它什么时候结束，不接续正在生成的实时文字（那是刻意的：见 spec 的「超出范围」）。
 */
export const RUN_WATCH_INTERVAL_MS = 3000

/**
 * 流被中断后，读回放（判断服务端还在不在跑）的尝试次数上限。
 *
 * 单副本「先停后起」或手动重启容器时，从旧进程停止接受到新进程能服务之间有几十秒的
 * 不可达窗口；这个重连轮询就是用来跨过它的。
 */
export const RUN_RECOVER_ATTEMPTS = 5

/** 两次读回放之间的间隔（约 5 次 × 6 秒 ≈ 30 秒）。见 `RUN_RECOVER_ATTEMPTS`。 */
export const RUN_RECOVER_INTERVAL_MS = 6000

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
export function useAgentChat({
  stream = streamAgentChat,
  loadThreadMessages = getAgentThreadMessages,
  stopRun = stopAgentRun,
  runWatchIntervalMs = RUN_WATCH_INTERVAL_MS,
  runRecoverAttempts = RUN_RECOVER_ATTEMPTS,
  runRecoverIntervalMs = RUN_RECOVER_INTERVAL_MS,
  getScopeError = () => null,
  saveScope = updateAgentThreadScope,
  onThreadCreated,
}: UseAgentChatOptions = {}) {
  const draft = ref('')
  // 用深层 ref 而不是检索页那样的 shallowRef：流式过程要原地改写最后一轮的 answer 和
  // traces，shallowRef 只跟踪整个数组的替换，逐 token 追加不会触发渲染。对话对象很小
  // （几轮 × 几百字），深层代理的开销远小于「每个 token 复制一遍整个数组」。
  const turns = ref<AgentTurn[]>([])
  const status = ref<AgentChatStatus>('idle')
  const inputError = ref<string | null>(null)
  const threadId = ref<string | null>(null)
  // 回放状态与流式状态分开：一个是「历史读出来了吗」，一个是「这一轮在生成吗」。合成一个
  // status 会让「正在读历史」误触发输入框禁用之外的流式 UI（停止按钮、光标）。
  const isLoadingThread = ref(false)
  const threadError = ref<AgentErrorPresentation | null>(null)
  // 早期历史被压缩掉时为真。界面必须如实说明，不能让人以为看到的就是全部。
  const isHistoryTruncated = ref(false)
  const historySummary = ref<string | null>(null)
  const historySyncError = ref<string | null>(null)
  // 服务端报告的「这个会话有在途运行」。刷新或切回来时，它是唯一能区分「这一轮没有回答」与
  // 「这一轮还在生成」的渠道：在途那一轮还没落库，从消息里看不出来。
  const awaitingRunId = ref<string | null>(null)
  const selection = ref<KnowledgeBaseSelection>({ mode: 'all' })
  const savingScope = ref(false)
  const scopeSaveError = ref<string | null>(null)
  let scopeEditVersion = 0
  let pendingScopeSave: Promise<void> = Promise.resolve()

  let runSequence = 0
  let loadSequence = 0
  let watchSequence = 0
  let activeController: AbortController | null = null
  let activeLoadController: AbortController | null = null
  // 用户已点停止、但还没拿到运行 id。``run_started`` 是第一个事件，这个窗口极小，
  // 但留一个意图位比让那次点击静默丢掉要好。
  let stopPending = false

  const remainingCharacters = computed(() => MAX_MESSAGE_CHARACTERS - draft.value.length)
  // 读历史期间也不许发送：那时 threadId 还没设上，发出去会被当成新会话，用户以为自己在
  // 续聊、实际上开了一个新的，而且旧会话的历史马上会覆盖掉界面。
  const canSend = computed(
    () =>
      draft.value.trim().length > 0 &&
      status.value !== 'streaming' &&
      !isLoadingThread.value &&
      !savingScope.value &&
      // 服务端还在跑这一轮时不能发：发出去会被它以 409 拒掉，而界面上看起来就像模型出错。
      awaitingRunId.value === null &&
      !getScopeError(),
  )
  const isStreaming = computed(() => status.value === 'streaming')
  /** 服务端确认这个会话有运行在跑——但当前这条连接不是它的订阅者（刚刷新/刚切回来）。 */
  const isAwaitingRun = computed(() => awaitingRunId.value !== null)
  /**
   * 服务端手上确实有一次可以停的运行：流式连接在看它，或界面处在「等待中」。
   *
   * 等待态没有本地连接，停止只能用回放给出的在途运行 id。id 为空时没有东西可停，
   * 所以那种情况不渲染停止键。
   */
  const canStop = computed(() => isStreaming.value || awaitingRunId.value !== null)

  watch(draft, (value) => {
    if (inputError.value && !validateMessage(value) && !getScopeError()) {
      inputError.value = null
    }
  })

  async function send(): Promise<void> {
    inputError.value = validateMessage(draft.value) || getScopeError()
    if (
      inputError.value ||
      status.value === 'streaming' ||
      isLoadingThread.value ||
      savingScope.value ||
      // 服务端还在跑这一轮时不能发（见 canSend 的说明）。retry() 也会走到这里，所以这一道
      // 不能只写在 canSend 里。
      awaitingRunId.value !== null
    )
      return

    const question = draft.value.trim()
    const submittedSelection = copySelection(selection.value)
    const submittedScopeVersion = scopeEditVersion
    const runId = ++runSequence
    const controller = new AbortController()
    activeController = controller

    turns.value.push(createTurn(question))
    // 取回代理对象而不是用上面那个原始对象：深层 ref 里只有代理上的写入会触发渲染。
    const live = turns.value[turns.value.length - 1]!

    draft.value = ''
    threadError.value = null
    status.value = 'streaming'

    try {
      for await (const event of stream({
        message: question,
        threadId: threadId.value,
        scope: submittedSelection,
        signal: controller.signal,
      })) {
        // 已被取消或已被更新的一轮不再往界面上写：break 会走生成器的 finally，
        // 顺带取消 reader、关掉连接。
        if (runId !== runSequence) break
        applyEvent(live, event)
        if (event.event === 'run_started' && submittedScopeVersion !== scopeEditVersion) {
          // 新会话还没拿到 ID 时发生的改选，也要保存为下一次提问的选择。
          void persistSelection(event.thread_id, copySelection(selection.value), scopeEditVersion)
        }
        if (event.event === 'done' || event.event === 'error') break
      }

      if (runId !== runSequence) return

      if (live.status === 'streaming') {
        // 已收到的片段保留，但没有服务端终态就不能当作完整答案。
        live.status = 'incomplete'
        settlePendingTraces(live, FAILED_TRACE_NOTE)
      } else if (live.status === 'done' || live.status === 'incomplete') {
        await synchronizeHistory(runId)
      }
    } catch (error) {
      if (runId !== runSequence) return

      // 连接被中断不一定是失败：部署切换会断掉订阅连接，而运行本身还在服务端跑。
      // 先读一次回放，服务端仍报有在途运行就转成等待态，而不是把这轮标成错误。
      if (await recoverInterruptedRun(runId)) return

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

  function acceptThreadId(id: string | null): void {
    const created = threadId.value === null && id !== null
    threadId.value = id
    if (created) onThreadCreated?.(id)
  }

  function applyEvent(turn: AgentTurn, event: AgentChatEvent): void {
    switch (event.event) {
      case 'run_started':
        acceptThreadId(event.thread_id)
        turn.runId = event.run_id
        turn.scope = event.scope
        scopeSaveError.value = null
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

  /** 逐次保存有效选择，防止快速改选的旧请求晚到后覆盖新选择。 */
  function persistSelection(
    targetThreadId: string,
    value: KnowledgeBaseSelection,
    version: number,
  ): Promise<void> {
    if (!isSelection(value)) return Promise.resolve()
    savingScope.value = true
    pendingScopeSave = pendingScopeSave.then(async () => {
      try {
        await saveScope(targetThreadId, value)
        if (version === scopeEditVersion && threadId.value === targetThreadId)
          scopeSaveError.value = null
      } catch {
        if (version === scopeEditVersion && threadId.value === targetThreadId)
          scopeSaveError.value = '范围保存未确认；请重新选择，或重新打开会话核对。'
      } finally {
        if (version === scopeEditVersion) savingScope.value = false
      }
    })
    return pendingScopeSave
  }

  function updateSelection(value: KnowledgeBaseSelection): Promise<void> {
    selection.value = copySelection(value)
    scopeEditVersion += 1
    scopeSaveError.value = null
    savingScope.value = false
    return threadId.value
      ? persistSelection(threadId.value, copySelection(value), scopeEditVersion)
      : Promise.resolve()
  }

  /** 流结束后只同步当前 checkpoint，不重新问模型，也不重读原文改写旧回答。 */
  async function synchronizeHistory(runId = runSequence): Promise<void> {
    const target = threadId.value
    if (!target) return
    try {
      const replay = await loadThreadMessages(target, activeController?.signal)
      if (runId !== runSequence || target !== threadId.value) return
      applyReplay(replay, target)
      historySyncError.value = null
    } catch {
      if (runId === runSequence && target === threadId.value)
        historySyncError.value = '会话状态同步失败，当前可能仍显示已压缩的临时记录。请重试同步。'
    }
  }

  /**
   * 把一份回放结果灌进界面，并在服务端还报「有运行在途」时开始等它结束。
   *
   * 两条路会用到（打开会话、一次运行结束后的同步），所以只有这一处实现。「还有在途运行」必须在这里
   * 接上轮询：漏了的话界面会停在「禁用发送」上而且没人去解开它。
   */
  function applyReplay(replay: AgentThreadMessagesDto, targetThreadId: string): void {
    turns.value = turnsFromReplay(replay.turns ?? [], HISTORY_TRACE_NOTE)
    isHistoryTruncated.value = replay.summarized
    historySummary.value = replay.summary ?? null
    awaitingRunId.value = replay.active_run_id ?? null
    if (awaitingRunId.value !== null) void watchPendingRun(targetThreadId)
  }

  /**
   * 等这个会话的在途运行结束，然后换成最终内容。
   *
   * **刻意不设放弃上限**：失活判定（服务端那条超时释放）会结束服务端的上报，所以这个轮询必然终止。
   * 而前端设的上限如果短于服务端的失活窗口，就会出现界面与服务端相反的情况——界面已经放弃「正在
   * 生成」，服务端却仍以「还在生成中」拒绝提交。
   *
   * 轮询只用来知道它什么时候结束，**不接续正在生成的实时文字**（与 ChatGPT / Claude 一致）：
   * 刷新后能看到「正在生成」并等它结束，但中间的字不会实时补发。
   */
  async function watchPendingRun(targetThreadId: string): Promise<void> {
    const watchId = ++watchSequence
    while (threadId.value === targetThreadId && watchId === watchSequence) {
      await new Promise((resolve) => setTimeout(resolve, runWatchIntervalMs))
      if (threadId.value !== targetThreadId || watchId !== watchSequence) return
      try {
        const replay = await loadThreadMessages(targetThreadId)
        if (threadId.value !== targetThreadId || watchId !== watchSequence) return
        turns.value = turnsFromReplay(replay.turns ?? [], HISTORY_TRACE_NOTE)
        isHistoryTruncated.value = replay.summarized
        historySummary.value = replay.summary ?? null
        awaitingRunId.value = replay.active_run_id ?? null
        if (awaitingRunId.value === null) return
      } catch {
        // 一次读失败不放弃：这是一条「等它结束」的循环，下一轮再试就好。
      }
    }
  }

  /**
   * 流被中断时的回退：读回放，服务端仍报有在途运行就进入等待态。
   *
   * 部署切换会把订阅连接断掉，但运行与连接已经解耦，服务端照旧跑完并落库。断流之后
   * 读回放能拿到服务端记的在途运行 id，界面显示「正在生成」并继续轮询；服务端不再上报
   * 之后按回放渲染这一轮。
   *
   * **读回放本身也要重试。** 单副本「先停后起」与手动重启容器期间，旧进程已停止接受
   * 连接、新进程还没起来，回放请求必然失败；这个窗口有几十秒，一次探针不够。所以按
   * `runRecoverAttempts` 次重试（默认约 30 秒）。**只有用尽重试仍连不上**才返回 ``false``
   * ——那更像真的断网，而不是一次部署，交给调用方走错误分支。
   *
   * 陈旧响应守卫与 ``synchronizeHistory`` 同理：这一轮已经被取消或换掉时什么都不做，但也不
   * 再标错。
   */
  async function recoverInterruptedRun(runId: number): Promise<boolean> {
    const target = threadId.value
    if (!target) return false
    for (let attempt = 0; attempt < runRecoverAttempts; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) => setTimeout(resolve, runRecoverIntervalMs))
        if (runId !== runSequence || target !== threadId.value) return true
      }
      try {
        const replay = await loadThreadMessages(target)
        if (runId !== runSequence || target !== threadId.value) return true
        applyReplay(replay, target)
        return true
      } catch {
        // 服务可能正在停-起切换，给它时间再试；到上限仍旧失败才当真的连不上。
      }
    }
    return false
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
    if (status.value === 'streaming') {
      const last = turns.value[turns.value.length - 1]
      if (!threadId.value || !last?.runId) {
        // run_started 还没到：记下意图，首帧到了再补发。
        stopPending = true
        return
      }
      requestStop(threadId.value, last.runId)
      return
    }
    // 等待态（刷新或断开回退之后在等这一轮结束）：停止用回放给出的在途运行 id。
    // 没有在途 id 就不渲染停止键，所以这里不需要兜底分支。
    if (threadId.value && awaitingRunId.value !== null) {
      requestStop(threadId.value, awaitingRunId.value)
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
    // 也不再等上一个会话的在途运行。
    watchSequence += 1

    const loadId = ++loadSequence
    const controller = new AbortController()
    activeLoadController = controller

    threadId.value = null
    turns.value = []
    draft.value = ''
    inputError.value = null
    threadError.value = null
    isHistoryTruncated.value = false
    historySummary.value = null
    historySyncError.value = null
    awaitingRunId.value = null
    scopeSaveError.value = null
    scopeEditVersion += 1
    savingScope.value = false
    status.value = 'idle'
    isLoadingThread.value = true

    try {
      const replay = await loadThreadMessages(targetThreadId, controller.signal)
      if (loadId !== loadSequence) return

      threadId.value = targetThreadId
      applyReplay(replay, targetThreadId)
      selection.value = copySelection(replay.scope)
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
    // 新会话不继承上一个会话的在途状态。
    watchSequence += 1
    turns.value = []
    draft.value = ''
    threadId.value = null
    inputError.value = null
    threadError.value = null
    isHistoryTruncated.value = false
    historySummary.value = null
    historySyncError.value = null
    awaitingRunId.value = null
    selection.value = { mode: 'all' }
    scopeEditVersion += 1
    savingScope.value = false
    scopeSaveError.value = null
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
    stopPending = false
    activeController?.abort()
    activeController = null
  }

  onScopeDispose(() => {
    cancelActiveRun()
    cancelActiveLoad()
    watchSequence += 1
  })

  return {
    draft,
    turns,
    status,
    inputError,
    threadId,
    isLoadingThread,
    threadError,
    isHistoryTruncated,
    historySummary,
    historySyncError,
    synchronizeHistory,
    selection,
    savingScope,
    scopeSaveError,
    updateSelection,
    remainingCharacters,
    canSend,
    canStop,
    isStreaming,
    isAwaitingRun,
    send,
    cancel,
    abandonRun,
    retry,
    loadThread,
    startNewConversation,
  }
}
