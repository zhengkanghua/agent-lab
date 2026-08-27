export type ClientErrorCode =
  'network_error' | 'request_timeout' | 'validation_error' | 'response_invalid' | 'unknown_error'

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

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (isRecord(error) && error.name === 'AbortError')
  )
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
