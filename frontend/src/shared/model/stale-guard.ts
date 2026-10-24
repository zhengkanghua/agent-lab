/**
 * 陈旧响应守卫：一次提交 / 一轮运行到底还算不算数，只由这里的序号说了算。
 *
 * 为什么必须靠序号，而不是只靠 abort：`AbortController` 只能拦住还没 resolve 的读取。
 * 「事件已经拿到、`await` 还没恢复执行」这个窗口里 abort 不起作用——那一轮早已被判作废，
 * 但它的代码还在往下走，只有一次序号比较能拦住它继续往界面上写字。所以守卫同时持有
 * 自增序号与当前 `AbortController`：abort 负责断开连接（省掉没人要的结果），序号负责判断
 * 归属（挡住已经在半路上的那一轮）。
 *
 * 对外只有两个动作：`begin()` 提交时取这一轮的令牌，`cancel()` 放弃当前这一轮。请求结束
 * 时调令牌自己的 `release()`。它不依赖 Vue，也不需要别的东西——生命周期仍归各自的
 * composable，这里只回答「还算不算数」。
 */

/** 一轮的令牌：带着这一轮请求要挂的信号，并回答这一轮是否仍是当前那一轮。 */
export interface StaleGuardToken {
  /** 这一轮的请求要挂的信号；`begin()` 之后被任何一次 `begin()` / `cancel()` 作废。 */
  readonly signal: AbortSignal
  /** 本轮是否仍是当前那一轮；陈旧判断只有这一条。 */
  isCurrent(): boolean
  /** 请求结束时调用；仅当本轮仍是当前那一轮才释放自己占的控制器。 */
  release(): void
}

export interface StaleGuard {
  /** 序号 +1、作废上一轮、建新控制器，返回本轮令牌。 */
  begin(): StaleGuardToken
  /** 序号 +1、abort、置空：放弃当前这一轮。 */
  cancel(): void
}

export function createStaleGuard(): StaleGuard {
  let sequence = 0
  let controller: AbortController | null = null

  function cancel(): void {
    sequence += 1
    controller?.abort()
    controller = null
  }

  return {
    begin(): StaleGuardToken {
      sequence += 1
      // 这一下同时覆盖两种情形：检索页是「新提交作废上一条」；对话页是「上一轮已经被
      // cancel() 清空，这里落空」——它在提交时从不主动掐上一轮，别照着这一行去改它的语义。
      controller?.abort()
      const current = new AbortController()
      controller = current
      const tokenId = sequence

      return {
        signal: current.signal,
        isCurrent: () => tokenId === sequence,
        release: () => {
          // 仅当自己仍是当前那一轮才释放：陈旧的一轮收尾时不许把新一轮的控制器清掉，
          // 否则随后那次 cancel() 就 abort 不到新一轮。
          if (tokenId === sequence) controller = null
        },
      }
    },
    cancel,
  }
}
