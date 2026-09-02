import { isRecord } from './json-guards'

export const API_REQUEST_TIMEOUT_MS = 45_000

interface ApiErrorBody {
  code?: unknown
  detail?: unknown
  retryable?: unknown
}

export interface RequestOptions {
  notifyUnauthorized?: boolean
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

/**
 * 归一化后的接口异常。
 *
 * `status` 缺省记 0，表示「请求没拿到 HTTP 响应」（网络不通、超时、被取消）。这个 0 是有语义的：
 * 登录页靠它区分「连不上服务器」和「服务器拒绝了本次登录」。改动缺省值会静默改掉登录页的
 * 文案分支——不报错,只是提示变成误导。
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string
  readonly retryable: boolean

  constructor(options: {
    message: string
    status?: number
    code: string
    retryable?: boolean
    cause?: unknown
  }) {
    super(options.message, { cause: options.cause })
    this.name = 'ApiError'
    this.status = options.status ?? 0
    this.code = options.code
    this.detail = options.message
    this.retryable = options.retryable ?? false
  }
}

/**
 * 判断异常是否来自 AbortController 主动取消，用于把「我们自己取消的」与真实故障分开。
 *
 * 只认 DOMException：浏览器与 jsdom 的 fetch 在信号中止时抛的都是标准
 * DOMException。曾另有一支兼容「name 为 AbortError 的普通 Error」的运行时，
 * 已按项目只跑在浏览器的前提删除；若将来这些 api 函数要在 Node 里直跑，
 * 需要连同该环境的实际抛出类型一起重新判断。
 */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/**
 * 拼出接口的完整 URL。
 *
 * 导出而不是留成模块私有：SSE 流式接口不能走 requestApi（它会 `await response.text()`
 * 把整条流读完才返回），但必须和其余接口用同一个 base 前缀，否则改 VITE_API_BASE_URL
 * 时会漏掉流式那一条。
 */
export function resolveApiUrl(path: string): string {
  return `${apiBaseUrl}${path}`
}

/**
 * 把一个非 2xx 响应翻译成 ApiError，并在 401 时通知应用。
 *
 * `body` 由调用方读取后传入：流式接口在这一步之后还要继续读 body 作为流，所以不能由
 * 本函数代读。code 的兜底顺序与 status 分支和 JSON 接口完全一致——两条路径共用这段，
 * 才能保证同一个后端错误在检索页和对话页拿到同一个 code。
 */
export function toApiError(
  response: Response,
  body: unknown,
  options: RequestOptions = {},
): ApiError {
  if (response.status === 401 && options.notifyUnauthorized !== false) {
    unauthorizedHandler?.()
  }
  const errorBody = isRecord(body) ? (body as ApiErrorBody) : null
  return new ApiError({
    message:
      typeof errorBody?.detail === 'string'
        ? errorBody.detail
        : 'The search service rejected the request.',
    status: response.status,
    code:
      typeof errorBody?.code === 'string'
        ? errorBody.code
        : response.status === 422
          ? 'validation_error'
          : response.status === 401
            ? 'authentication_required'
            : response.status === 403
              ? 'permission_denied'
              : 'unknown_error',
    retryable:
      typeof errorBody?.retryable === 'boolean' ? errorBody.retryable : response.status >= 500,
  })
}

export async function requestJson<T>(
  path: string,
  init: RequestInit,
  options: RequestOptions = {},
): Promise<T> {
  const body = await requestApi(path, init, options)
  if (body === undefined) {
    throw new ApiError({
      message: 'The service returned an empty response.',
      code: 'response_invalid',
    })
  }
  return body as T
}

export async function requestVoid(
  path: string,
  init: RequestInit,
  options: RequestOptions = {},
): Promise<void> {
  await requestApi(path, init, options)
}

async function requestApi(
  path: string,
  init: RequestInit,
  options: RequestOptions,
): Promise<unknown> {
  let timedOut = false
  const requestController = new AbortController()
  const callerSignal = init.signal
  const abortFromCaller = () => requestController.abort()
  const timeoutId = setTimeout(() => {
    timedOut = true
    requestController.abort()
  }, API_REQUEST_TIMEOUT_MS)

  if (callerSignal?.aborted) {
    abortFromCaller()
  } else {
    callerSignal?.addEventListener('abort', abortFromCaller, { once: true })
  }

  try {
    let response: Response

    try {
      const headers = new Headers(init.headers)
      if (!headers.has('Accept')) headers.set('Accept', 'application/json')
      if (typeof init.body === 'string' && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json')
      }
      response = await fetch(resolveApiUrl(path), {
        ...init,
        credentials: 'same-origin',
        headers,
        signal: requestController.signal,
      })
    } catch (error) {
      if (timedOut && !callerSignal?.aborted) {
        throw new ApiError({
          message: 'The search request timed out.',
          code: 'request_timeout',
          retryable: true,
          cause: error,
        })
      }
      if (isAbortError(error)) {
        throw error
      }
      throw new ApiError({
        message: 'Unable to reach the search service.',
        code: 'network_error',
        retryable: true,
        cause: error,
      })
    }

    const body = await readJsonBody(response)

    if (!response.ok) {
      throw toApiError(response, body, options)
    }

    return body
  } catch (error) {
    if (timedOut && !callerSignal?.aborted && isAbortError(error)) {
      throw new ApiError({
        message: 'The search request timed out.',
        code: 'request_timeout',
        retryable: true,
        cause: error,
      })
    }
    throw error
  } finally {
    clearTimeout(timeoutId)
    callerSignal?.removeEventListener('abort', abortFromCaller)
  }
}

/**
 * 读响应体并按 JSON 解析；空体返回 undefined。
 *
 * 失败响应解析不出 JSON 时返回 undefined 而不是抛错：那种情况下（例如反向代理返回的
 * HTML 错误页）真正要告诉用户的是 HTTP 状态码，解析异常本身没有信息量。
 */
export async function readJsonBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) {
    return undefined
  }

  try {
    return JSON.parse(text) as unknown
  } catch (error) {
    if (response.ok) {
      throw new ApiError({
        message: 'The search service returned an unreadable response.',
        status: response.status,
        code: 'response_invalid',
        cause: error,
      })
    }
    return undefined
  }
}
