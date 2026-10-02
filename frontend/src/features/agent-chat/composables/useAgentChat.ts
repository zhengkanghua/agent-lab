import { onScopeDispose, watch } from 'vue'
import { stopAgentRun, streamAgentChat } from '@/api/agent-chat'
import { getAgentThreadMessages, updateAgentThreadScope } from '@/api/agent-threads'
import { validateMessage } from '../model/agent-validation'
import { clearConversation, createChatState } from './chat-state'
import { useChatScope } from './useChatScope'
import { useChatStream, type AgentChatStream, type AgentRunStopper } from './useChatStream'
import { useThreadHistory, type AgentThreadLoader } from './useThreadHistory'

export type { AgentChatStream, AgentRunStopper, AgentThreadLoader }

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
 * 编排一次多轮 Agent 对话。三个关注点各自成文件，这里只负责组装与会话级的复位。
 *
 *   - `useChatStream`：一次运行的生命周期（发起、读事件、停止、取消、重发）；
 *   - `useThreadHistory`：历史读取、跑完后的对账、等待在途运行、断流回退；
 *   - `useChatScope`：会话的知识库范围与逐次保存。
 *
 * 拆开的原因不是「文件太长」，而是原来那一个文件同时管着七件事，改任何一件都要在一个 635 行
 * 的文件里穿行。三者共享的只有 chat-state 里那五个 ref（草稿、轮次、状态、输入错误、会话 id），
 * 依赖方向是单向的：流 → 历史，流 → 范围。这条边是拆之前就存在的（跑完一轮要同步历史、
 * run_started 要保存范围），只是原先没有形状；现在它写在 useChatStream 的签名里。
 *
 * 检索页的 `useSearchStream` 做多轮累积的方式与这里相似，但两套实现刻意不共用：检索流每一轮是
 * 独立、离散的一次搜索，没有跨轮状态；Agent 每一轮要持续改写最后一轮的流式回答、还要按模型与
 * tool 轨迹重建，且历史来自服务端回放。硬套会让两边都变形。
 */
export function useAgentChat({
  stream: streamImpl = streamAgentChat,
  loadThreadMessages = getAgentThreadMessages,
  stopRun = stopAgentRun,
  runWatchIntervalMs,
  runRecoverAttempts,
  runRecoverIntervalMs,
  getScopeError = () => null,
  saveScope = updateAgentThreadScope,
  onThreadCreated,
}: UseAgentChatOptions = {}) {
  const state = createChatState()

  const scope = useChatScope({
    getThreadId: () => state.threadId.value,
    saveScope,
  })

  const history = useThreadHistory(state, {
    // 切会话等于放弃在途的那一轮。用闭包取，因为两边本来就是一体的：
    // 历史要停掉流，流要读历史（对账、断流回退）。
    cancelRun: () => stream.cancelActiveRun(),
    resetScope: () => scope.reset(),
    adoptScope: scope.adopt,
    loadThreadMessages,
    runWatchIntervalMs,
    runRecoverAttempts,
    runRecoverIntervalMs,
  })

  const stream = useChatStream(state, scope, history, {
    stream: streamImpl,
    stopRun,
    getScopeError,
    onThreadCreated,
  })

  watch(state.draft, (value) => {
    if (state.inputError.value && !validateMessage(value) && !getScopeError()) {
      state.inputError.value = null
    }
  })

  /**
   * 开一个新会话。
   *
   * 必须同时丢掉 threadId：只清界面不换 id 的话，下一轮仍会带上旧 thread，模型看得见
   * 用户以为已经删掉的历史。
   */
  function startNewConversation(): void {
    stream.cancelActiveRun()
    history.cancelActiveLoad()
    history.resetHistory()
    clearConversation(state)
    scope.reset()
    scope.adopt({ mode: 'all' })
  }

  onScopeDispose(() => {
    stream.cancelActiveRun()
    history.dispose()
  })

  return {
    draft: state.draft,
    turns: state.turns,
    status: state.status,
    inputError: state.inputError,
    threadId: state.threadId,
    isLoadingThread: history.isLoadingThread,
    threadError: history.threadError,
    isHistoryTruncated: history.isHistoryTruncated,
    historySummary: history.historySummary,
    historySyncError: history.historySyncError,
    synchronizeHistory: history.synchronizeHistory,
    selection: scope.selection,
    savingScope: scope.savingScope,
    scopeSaveError: scope.scopeSaveError,
    updateSelection: scope.updateSelection,
    remainingCharacters: stream.remainingCharacters,
    canSend: stream.canSend,
    canStop: stream.canStop,
    isReconnecting: history.isReconnecting,
    isStreaming: stream.isStreaming,
    isAwaitingRun: history.isAwaitingRun,
    send: stream.send,
    cancel: stream.cancel,
    abandonRun: stream.abandonRun,
    retry: stream.retry,
    loadThread: history.loadThread,
    startNewConversation,
  }
}
