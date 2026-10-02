import { ref, type Ref } from 'vue'
import type { AgentChatStatus } from '../model/agent-validation'
import type { AgentTurn } from '../model/conversation'

/* 一次对话里「看得见的那部分」状态。
 *
 * 单独抽出来不是为了少写几行，而是因为它就是 useAgentChat 拆不动的原因：流式那一轮要往
 * turns 里追加并原地改写最后一轮，打开会话要整段替换 turns，范围持久化要读当前 threadId，
 * 载入历史又要把 draft / inputError / status 一起清干净。三个关注点共享的正是这五个 ref，
 * 所以把它们摆成一个显式对象传下去——谁碰了哪几个状态，看签名就知道，不用通读整个文件。
 */

export interface ChatState {
  /** 输入草稿。 */
  draft: Ref<string>
  /**
   * 轮次列表。用深层 ref 而不是 shallowRef：流式过程要原地改写最后一轮的 answer 与 traces，
   * shallowRef 只跟踪整个数组的替换，逐 token 追加不会触发渲染。对话对象很小（几轮 × 几百字），
   * 深层代理的开销远小于「每个 token 复制一遍整个数组」。
   */
  turns: Ref<AgentTurn[]>
  status: Ref<AgentChatStatus>
  inputError: Ref<string | null>
  /** 当前会话 id；为 null 表示「还没在服务端建过会话」。 */
  threadId: Ref<string | null>
}

export function createChatState(): ChatState {
  return {
    draft: ref(''),
    turns: ref<AgentTurn[]>([]),
    status: ref<AgentChatStatus>('idle'),
    inputError: ref<string | null>(null),
    threadId: ref<string | null>(null),
  }
}

/**
 * 把「看得见的这段对话」清干净。换会话与开新会话共用。
 *
 * 只清这四个加 status：历史相关的那些（截断标记、同步错误、在途运行 id）归
 * useThreadHistory.resetHistory()，范围选择归 useChatScope.reset()。三边各清各的，
 * 免得「清了一半」这种状态散在两个文件里。
 */
export function clearConversation(state: ChatState): void {
  state.threadId.value = null
  state.turns.value = []
  state.draft.value = ''
  state.inputError.value = null
  state.status.value = 'idle'
}
