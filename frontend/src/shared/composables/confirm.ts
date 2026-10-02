import { readonly, ref } from 'vue'

/* 破坏性操作的确认请求队列（单槽）。
 *
 * 为什么是模块级单例而不是每处自己持有一个 ref：确认框的调用方大半是 feature 里的
 * composable（删会话、注销账号、撤销会话），它们没有自己的视图层，只能把「弹一个确认框」
 * 这个动作委托出去，拿回来的必须是「用户点了什么」。用 window.confirm 时这条链路天然成立
 * ——它是同步的、返回布尔值；换成自绘弹窗后，能让调用点保持同样写法的只有 Promise。
 *
 * 视图在 shared/ui/ConfirmDialog.vue，挂在 App.vue 上一次，因此任何页面、任何 feature
 * 都能用同一套确认框，不需要各自在模板里再摆一个（那会让 5 个调用点又长出 5 份状态）。
 *
 * 与 authSession 同类：应用级单例，由 shared 拥有，不参与「feature 之间禁止互相导入」。
 */

export interface ConfirmRequest {
  /** 对话框标题，同时作为读屏播报的对话框名。 */
  title: string
  /** 后果说明。破坏性操作要把「不能撤销」这类关键事实写在这里。 */
  description?: string
  /**
   * 确认键文案。写具体动作（「删除会话」）而不是「确定」：后者读完不知道点了会发生什么，
   * 而这一键的后果是不可恢复的。
   */
  confirmLabel: string
  cancelLabel?: string
  /** 破坏性操作用 danger：确认键是砖红填充，与「安全出口」在颜色上先分开。 */
  tone?: 'danger' | 'default'
}

interface PendingConfirm extends ConfirmRequest {
  resolve: (confirmed: boolean) => void
}

const pending = ref<PendingConfirm | null>(null)

/** 当前待确认的请求。ConfirmDialog 用它决定渲染什么、是否打开。 */
export const pendingConfirm = readonly(pending)

/**
 * 弹一个确认框，等用户给出决定。
 *
 * 同一时刻只保留一个请求：新的请求会把上一个按「取消」结掉（resolve false），而不是排队。
 * 排队意味着第二次点击的后果会出现在一个已经变了的上下文里；而按「取消」结掉只是什么都不做，
 * 不会静默执行任何破坏性动作，是两者里安全的那一边。
 */
export function requestConfirm(request: ConfirmRequest): Promise<boolean> {
  pending.value?.resolve(false)

  return new Promise<boolean>((resolve) => {
    pending.value = { ...request, resolve }
  })
}

/** 用户在确认框上做出了选择。重复调用无副作用。 */
export function settleConfirm(confirmed: boolean): void {
  const current = pending.value
  if (!current) return
  pending.value = null
  current.resolve(confirmed)
}

/**
 * 清掉悬挂的请求（按「取消」结掉）。
 *
 * 给测试用：模块级状态会跨用例存活，不清的话上一个用例留下的待确认请求会让下一个用例
 * 的弹窗一打开就是打开态。另外也适合挂在卸载路径上。
 */
export function resetConfirm(): void {
  settleConfirm(false)
}
