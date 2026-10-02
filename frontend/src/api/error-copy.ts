import { ApiError } from './client'

/**
 * 面向用户的错误文案：标题说明「出了什么事」，描述说明「接下来能做什么」。
 *
 * 各处的文案表可以在此基础上加字段（例如检索页要带 retryable），所以这里只约定
 * 两个所有位置都必须有的字段，不做成封闭类型。
 */
export interface ErrorCopy {
  title: string
  description: string
}

/**
 * 把一个异常翻译成该场景的用户文案。
 *
 * 判定顺序固定为 code → HTTP status → 兜底，四处调用方共用同一套优先级，避免各自
 * 手写「先看 code 还是先看 status」时又分叉。只提供其中一张表也可以，另一张传 `{}`。
 *
 * code 优先于 status 是有意的：code 描述的是具体失败原因，status 只是它的粗分类。
 * 例如 422 既可能来自应用级校验兜底（响应体没有 code，client.ts 合成
 * `validation_error`），也可能来自路由级脱敏（带 `invalid_request`），两者要落到
 * 各自的文案上，就必须让 code 先说话。
 *
 * 非 ApiError（例如渲染期的 TypeError）直接走兜底：那种异常没有对外契约里的
 * code 和 status，硬去分类只会给出误导性的文案。
 *
 * 共用的只有这套查表机制，文案表留在各自领域文件里：`features/semantic-search/model/search-error.ts`、
 * `features/agent-chat/model/agent-error.ts`、`features/user-admin/model/admin-error.ts`、
 * `DocumentReader.vue`、`LoginPage.vue`。新增错误展示位置时加一张表传进来，
 * 不要再手写 if-else 判定链——之前四处各写一套，依据还不一致（检索页混用 code 和 status、
 * 全文阅读只看 status、账号管理只看 code），新增错误码时容易只补其中一处。
 *
 * 两处刻意不走这里（2026-10 核对过，它们的规则不是「查表」，硬并会改变行为）：
 *   - `features/document-review/presentation.ts` 的 reviewError：把三类瞬时故障
 *     （超时/网络/响应无效）归成同一句「结果未确认」，其余一律显示服务端 detail；
 *   - `features/settings/model/account-error.ts` 的 getAccountErrorCopy：查表命中就用表，
 *     否则显示服务端 detail。
 *   共同点是「兜底要显示服务端 detail」，而本函数的兜底是一个固定值。要并进来就得给
 *   fallback 加一个「按 error 算」的函数式形态，只为这两处服务——按工程取舍 1，不划算。
 *   代价是：新增错误码时这两处要自己补，本函数的调用方不受影响。
 */
export function resolveErrorCopy<TCopy>(
  error: unknown,
  tables: {
    byCode?: Readonly<Partial<Record<string, TCopy>>>
    byStatus?: Readonly<Partial<Record<number, TCopy>>>
    fallback: TCopy
  },
): TCopy {
  if (!(error instanceof ApiError)) return tables.fallback
  return tables.byCode?.[error.code] ?? tables.byStatus?.[error.status] ?? tables.fallback
}
