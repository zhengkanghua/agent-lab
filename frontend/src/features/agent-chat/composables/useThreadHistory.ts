import { computed, ref } from 'vue'
import { getAgentThreadMessages, type AgentThreadMessagesDto } from '@/api/agent-threads'
import { ApiError, isAbortError } from '@/api/client'
import type { KnowledgeBaseSelection } from '@/api/knowledge-scope'
import { presentAgentError, type AgentErrorPresentation } from '../model/agent-error'
import { turnsFromReplay } from '../model/conversation'
import { clearConversation, type ChatState } from './chat-state'

/* 会话历史的读取、对账与「等那一轮跑完」。
 *
 * 从 useAgentChat 里分出来的一块，覆盖四条互相咬合的路径：打开会话（载入）、跑完一轮后的
 * 对账（同步）、刷新后等一个在途运行结束（轮询）、断流后的回退（重连重试）。它们共用同一份
 * 「服务端说这个会话有在途运行」的判断，也共用同一个陈旧响应守卫，拆开反而会各自养一份。
 */

/** 注入回放实现，测试里可以不打桩 fetch 就驱动整个状态机。 */
export type AgentThreadLoader = typeof getAgentThreadMessages

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

// 回放专用：历史里那次调用没有结果，是当时就断了，不是现在还在查。
const HISTORY_TRACE_NOTE = '这次工具调用没有结果记录，当时的对话中断了。'

/** 一次同步请求的上下文。 */
export interface SyncOptions {
  /** 同步请求的取消信号（本轮运行的 controller）。 */
  signal?: AbortSignal
  /**
   * 响应回来时判断「这一轮是否已被取消或换掉」。不传就只按会话身份判断——
   * 页面上的「同步会话状态」按钮走这条路，它没有归属的运行轮次。
   */
  isStale?: () => boolean
}

export interface UseThreadHistoryOptions {
  /** 放弃在途的那一轮运行。切会话等于放弃它，否则旧会话的 token 会继续写进新会话的界面。 */
  cancelRun: () => void
  /**
   * 复位范围选择的编辑版本与错误（打开会话时用）。**不动当前选择**——那个由回放决定，
   * 先复位成一个中间值会让范围选择器闪一下。
   */
  resetScope: () => void
  /** 打开会话成功后按回放里的范围落选择。 */
  adoptScope: (value: KnowledgeBaseSelection) => void
  loadThreadMessages?: AgentThreadLoader
  runWatchIntervalMs?: number
  runRecoverAttempts?: number
  runRecoverIntervalMs?: number
}

export function useThreadHistory(state: ChatState, options: UseThreadHistoryOptions) {
  const {
    cancelRun,
    resetScope,
    adoptScope,
    loadThreadMessages = getAgentThreadMessages,
    runWatchIntervalMs = RUN_WATCH_INTERVAL_MS,
    runRecoverAttempts = RUN_RECOVER_ATTEMPTS,
    runRecoverIntervalMs = RUN_RECOVER_INTERVAL_MS,
  } = options

  const isLoadingThread = ref(false)
  const threadError = ref<AgentErrorPresentation | null>(null)
  // 早期历史被压缩掉时为真。界面必须如实说明，不能让人以为看到的就是全部。
  const isHistoryTruncated = ref(false)
  const historySummary = ref<string | null>(null)
  const historySyncError = ref<string | null>(null)
  // 服务端报告的「这个会话有在途运行」。刷新或切回来时，它是唯一能区分「这一轮没有回答」与
  // 「这一轮还在生成」的渠道：在途那一轮还没落库，从消息里看不出来。
  const awaitingRunId = ref<string | null>(null)
  // 断流后正在重连（旧进程停了、新进程还没起来的那段窗口）。只在重试阶段为真，
  // 用来在对话底部显示「连接中断，正在重连」。
  const isReconnecting = ref(false)

  let loadSequence = 0
  let watchSequence = 0
  let activeLoadController: AbortController | null = null

  /** 服务端确认这个会话有运行在跑——但当前这条连接不是它的订阅者（刚刷新/刚切回来）。 */
  const isAwaitingRun = computed(() => awaitingRunId.value !== null)

  /**
   * 复位历史相关的状态（含「不再等上一个会话的在途运行」）。换会话与开新会话共用。
   * 看得见的那部分对话由 clearConversation 清。
   */
  function resetHistory(): void {
    watchSequence += 1
    threadError.value = null
    isHistoryTruncated.value = false
    historySummary.value = null
    historySyncError.value = null
    awaitingRunId.value = null
    isReconnecting.value = false
    isLoadingThread.value = false
  }

  function cancelActiveLoad(): void {
    loadSequence += 1
    activeLoadController?.abort()
    activeLoadController = null
  }

  /**
   * 流结束后只同步当前 checkpoint，不重新问模型，也不重读原文改写旧回答。
   *
   * 陈旧响应守卫：调用方传 isStale（流传入「这一轮是否已被取消」），页面上的同步按钮不传。
   * 两种情况都要挡的是「会话已经换了」。
   */
  async function synchronizeHistory({ signal, isStale }: SyncOptions = {}): Promise<void> {
    const target = state.threadId.value
    if (!target) return
    try {
      const replay = await loadThreadMessages(target, signal)
      if (target !== state.threadId.value || isStale?.()) return
      applyReplay(replay, target)
      historySyncError.value = null
    } catch {
      if (target === state.threadId.value && !isStale?.())
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
    state.turns.value = turnsFromReplay(replay.turns ?? [], HISTORY_TRACE_NOTE)
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
    while (state.threadId.value === targetThreadId && watchId === watchSequence) {
      await new Promise((resolve) => setTimeout(resolve, runWatchIntervalMs))
      if (state.threadId.value !== targetThreadId || watchId !== watchSequence) return
      try {
        const replay = await loadThreadMessages(targetThreadId)
        if (state.threadId.value !== targetThreadId || watchId !== watchSequence) return
        // 还在跑：界面保持不动。**不要每一轮都把 turns 换一遍**——turnsFromReplay 会给每一轮
        // 生成新的 id，transcript 以 turn.id 为 key，整段对话会被拆掉重建、滚动位置被反复重置
        // （表现是用户往下滚、下一次轮询又把他拉回去）。等它结束后一次性按回放渲染。
        if ((replay.active_run_id ?? null) !== null) continue
        state.turns.value = turnsFromReplay(replay.turns ?? [], HISTORY_TRACE_NOTE)
        isHistoryTruncated.value = replay.summarized
        historySummary.value = replay.summary ?? null
        awaitingRunId.value = null
        return
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
   * 陈旧响应守卫与 `synchronizeHistory` 同理：这一轮已经被取消或换掉时什么都不做，但也不
   * 再标错（所以那种情况返回 true，表示「不用走错误分支」）。
   */
  async function recoverInterruptedRun(isStale: () => boolean): Promise<boolean> {
    const target = state.threadId.value
    if (!target) return false
    try {
      for (let attempt = 0; attempt < runRecoverAttempts; attempt += 1) {
        if (attempt > 0) {
          // 第一次探针失败说明服务此刻不可达（最典型是停-起切换）；从这一轮开始告诉用户
          // 正在重连，界面底部会显示它。
          isReconnecting.value = true
          await new Promise((resolve) => setTimeout(resolve, runRecoverIntervalMs))
          if (isStale() || target !== state.threadId.value) return true
        }
        try {
          const replay = await loadThreadMessages(target)
          if (isStale() || target !== state.threadId.value) return true
          if (replay.active_run_id != null) {
            // 服务端确认还在跑：保留当前屏幕上的片段与滚动位置，只进入等待态。
            // **刻意不走 applyReplay**：那会用回放重建 turns（每轮都是新 id），transcript 以
            // turn.id 为 key，整段对话会被拆掉重建、滚动位置被重置。等它结束后再按回放渲染一次。
            awaitingRunId.value = replay.active_run_id
            void watchPendingRun(target)
            return true
          }
          applyReplay(replay, target)
          return true
        } catch {
          // 服务可能正在停-起切换，给它时间再试；到上限仍旧失败才当真的连不上。
        }
      }
      return false
    } finally {
      isReconnecting.value = false
    }
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
    cancelRun()
    cancelActiveLoad()

    const loadId = ++loadSequence
    const controller = new AbortController()
    activeLoadController = controller

    clearConversation(state)
    resetHistory()
    resetScope()
    isLoadingThread.value = true

    try {
      const replay = await loadThreadMessages(targetThreadId, controller.signal)
      if (loadId !== loadSequence) return

      state.threadId.value = targetThreadId
      applyReplay(replay, targetThreadId)
      adoptScope(replay.scope)
    } catch (error) {
      if (loadId !== loadSequence || isAbortError(error)) return

      // 打不开就退回「没有会话」的状态：threadId 留空，用户可以直接开始一段新对话。
      state.threadId.value = null
      threadError.value = presentAgentError(
        error instanceof ApiError
          ? error
          : new ApiError({
              message: '读取这个会话的记录出现了未预期的失败。',
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

  /** 卸载时停掉在途的载入与轮询。 */
  function dispose(): void {
    cancelActiveLoad()
    watchSequence += 1
  }

  return {
    isLoadingThread,
    threadError,
    isHistoryTruncated,
    historySummary,
    historySyncError,
    awaitingRunId,
    isAwaitingRun,
    isReconnecting,
    loadThread,
    synchronizeHistory,
    recoverInterruptedRun,
    resetHistory,
    cancelActiveLoad,
    dispose,
  }
}
