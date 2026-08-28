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
