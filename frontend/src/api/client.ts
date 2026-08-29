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
 * 文案分支——不报错，只是提示变成误导。
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
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
      response = await fetch(`${apiBaseUrl}${path}`, {
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
      if (response.status === 401 && options.notifyUnauthorized !== false) {
        unauthorizedHandler?.()
      }
      const errorBody = isRecord(body) ? (body as ApiErrorBody) : null
      throw new ApiError({
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

async function readJsonBody(response: Response): Promise<unknown> {
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
