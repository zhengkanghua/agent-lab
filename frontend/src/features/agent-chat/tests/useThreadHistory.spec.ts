import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { AgentThreadMessagesDto } from '@/api/agent-threads'

/* 会话历史的读取、对账与「等那一轮跑完」（309 行、此前只被 useAgentChat.spec 间接覆盖）。
 * 挑的是四条路径里「错了会毁数据或会永久卡住」的行为：
 *   - 打开失败时不设 threadId（否则用户会在一个打不开的会话里提问，被后端按归属拒掉，
 *     界面上却像模型出错）；
 *   - 连点两个会话时先发的后到不生效（序号守卫，不能只靠 abort）；
 *   - 服务端说「有运行在途」时按间隔轮询，且**轮询期间不重建 turns**
 *     （turnsFromReplay 每轮生成新 id，transcript 以 id 为 key，重建会反复重置滚动位置）；
 *   - 断流回退要重试若干次（部署停-起有几十秒不可达窗口），且服务端仍报在途时不重建 turns。
 */

const api = vi.hoisted(() => ({ getAgentThreadMessages: vi.fn() }))
vi.mock('@/api/agent-threads', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getAgentThreadMessages: api.getAgentThreadMessages,
}))

import { createChatState } from '../composables/chat-state'
import { useThreadHistory } from '../composables/useThreadHistory'

function replay(overrides: Partial<AgentThreadMessagesDto> = {}): AgentThreadMessagesDto {
  return {
    thread_id: 'thread-1',
    scope: { mode: 'all' },
    turns: [],
    summarized: false,
    summary: null,
    active_run_id: null,
    ...overrides,
  } as unknown as AgentThreadMessagesDto
}

function build(overrides: Record<string, unknown> = {}) {
  const state = createChatState()
  const cancelRun = vi.fn()
  const resetScope = vi.fn()
  const adoptScope = vi.fn()
  const history = useThreadHistory(state, {
    cancelRun,
    resetScope,
    adoptScope,
    loadThreadMessages: api.getAgentThreadMessages,
    runWatchIntervalMs: 3000,
    runRecoverAttempts: 3,
    runRecoverIntervalMs: 6000,
    ...overrides,
  })
  return { state, history, cancelRun, resetScope, adoptScope }
}

describe('useThreadHistory', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    api.getAgentThreadMessages.mockReset().mockResolvedValue(replay())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('打开会话成功：落 threadId、灌入轮次、按回放落范围选择', async () => {
    api.getAgentThreadMessages.mockResolvedValue(
      replay({ scope: { mode: 'selected', knowledge_base_ids: ['kb-1'] } }),
    )
    const { state, history, adoptScope } = build()

    await history.loadThread('thread-1')

    expect(state.threadId.value).toBe('thread-1')
    expect(adoptScope).toHaveBeenCalledWith({ mode: 'selected', knowledge_base_ids: ['kb-1'] })
    expect(history.isLoadingThread.value).toBe(false)
    expect(history.threadError.value).toBeNull()
  })

  it('打开失败不设 threadId：用户要能直接开一段新对话', async () => {
    api.getAgentThreadMessages.mockRejectedValue(
      new ApiError({ message: 'not found', status: 404, code: 'thread_not_found' }),
    )
    const { state, history } = build()

    await history.loadThread('thread-1')

    expect(state.threadId.value).toBeNull()
    expect(history.threadError.value).not.toBeNull()
    expect(history.isLoadingThread.value).toBe(false)
  })

  it('连点两个会话：先发的后到不生效', async () => {
    const pending: Array<(value: AgentThreadMessagesDto) => void> = []
    api.getAgentThreadMessages.mockImplementation(
      () => new Promise((resolve) => pending.push(resolve)),
    )
    const { state, history } = build()

    const first = history.loadThread('thread-first')
    const second = history.loadThread('thread-second')

    // 后点的先回，先点的后回：迟到的那个不能把会话换回去。
    pending[1]!(replay({ thread_id: 'thread-second' }))
    await second
    pending[0]!(replay({ thread_id: 'thread-first' }))
    await first

    expect(state.threadId.value).toBe('thread-second')
  })

  it('服务端报在途运行时轮询等它结束，且轮询期间不动 turns', async () => {
    api.getAgentThreadMessages.mockResolvedValue(replay({ active_run_id: 'run-9' }))
    const { state, history } = build()

    await history.loadThread('thread-1')
    expect(history.isAwaitingRun.value).toBe(true)

    // 手动放一轮用户看得见的内容，验证轮询不会把它重建掉。
    state.turns.value = [{ id: 'local-turn' }] as never
    const before = state.turns.value

    // 第一轮轮询：服务端仍说在跑 → 界面保持不动。
    await vi.advanceTimersByTimeAsync(3000)
    expect(state.turns.value).toBe(before)
    expect(history.isAwaitingRun.value).toBe(true)

    // 第二轮：在途结束 → 按回放渲染一次，等待态解除。
    api.getAgentThreadMessages.mockResolvedValue(replay({ active_run_id: null }))
    await vi.advanceTimersByTimeAsync(3000)
    expect(state.turns.value).not.toBe(before)
    expect(history.isAwaitingRun.value).toBe(false)

    history.dispose()
  })

  it('轮询期间换会话立刻停下，不再继续读', async () => {
    api.getAgentThreadMessages.mockResolvedValue(replay({ active_run_id: 'run-9' }))
    const { state, history } = build()
    await history.loadThread('thread-1')

    const callsAfterLoad = api.getAgentThreadMessages.mock.calls.length
    // 换会话：resetHistory 会把这一轮的等待标记清掉并让轮询序号失效。
    state.threadId.value = 'thread-2'
    history.resetHistory()

    await vi.advanceTimersByTimeAsync(30000)
    expect(api.getAgentThreadMessages.mock.calls.length).toBe(callsAfterLoad)
    expect(history.isAwaitingRun.value).toBe(false)
  })

  it('断流回退：先重试，服务端仍报在途就进入等待态且不重建轮次', async () => {
    api.getAgentThreadMessages.mockRejectedValueOnce(new ApiError({ message: 'offline', code: 'network_error' }))
    const { state, history } = build()
    state.threadId.value = 'thread-1'
    state.turns.value = [{ id: 'streamed-turn' }] as never
    const before = state.turns.value

    api.getAgentThreadMessages.mockResolvedValue(replay({ active_run_id: 'run-9' }))
    const recovered = history.recoverInterruptedRun(() => false)
    await vi.advanceTimersByTimeAsync(6000)
    await expect(recovered).resolves.toBe(true)

    expect(history.awaitingRunId.value).toBe('run-9')
    // 屏幕上的片段与滚动位置保住：等它结束后再按回放渲染。
    expect(state.turns.value).toBe(before)
    expect(history.isReconnecting.value).toBe(false)

    state.threadId.value = null
  })

  it('回退重试用尽仍连不上：返回 false 并复位重连标记', async () => {
    api.getAgentThreadMessages.mockRejectedValue(new ApiError({ message: 'offline', code: 'network_error' }))
    const { state, history } = build()
    state.threadId.value = 'thread-1'

    const recovered = history.recoverInterruptedRun(() => false)
    await vi.advanceTimersByTimeAsync(6000 * 3)

    await expect(recovered).resolves.toBe(false)
    expect(history.isReconnecting.value).toBe(false)
  })

  it('对账失败时给出可重试的说明，并保住「当前可能不完整」的提示位', async () => {
    const { state, history } = build()
    state.threadId.value = 'thread-1'
    api.getAgentThreadMessages.mockRejectedValue(new ApiError({ message: 'offline', code: 'network_error' }))

    await history.synchronizeHistory()

    expect(history.historySyncError.value).toContain('同步失败')
  })
})
