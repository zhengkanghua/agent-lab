import type { ApiError } from '../../../api/client'
import { resolveErrorCopy, type ErrorCopy } from '../../../api/error-copy'

export interface SearchErrorPresentation extends ErrorCopy {
  retryable: boolean
}

/** 文案表里的一条。省略 retryable 表示沿用 ApiError 自己带的重试语义。 */
interface SearchErrorCopy extends ErrorCopy {
  retryable?: boolean
}

const VALIDATION_COPY: SearchErrorCopy = {
  title: '检索内容未通过校验',
  description: '请检查输入是否为空或过长，然后重新搜索。',
  retryable: false,
}

const TIMEOUT_COPY: SearchErrorCopy = {
  title: '检索等待时间过长',
  description: '上游服务没有及时响应，可以稍后重试本次搜索。',
  retryable: true,
}

const UNAVAILABLE_COPY: SearchErrorCopy = {
  title: '检索服务暂时不可用',
  description: '新闻索引或语义服务当前无法连接，请稍后重试。',
}

const CONFIGURATION_COPY: SearchErrorCopy = {
  title: '检索服务尚未就绪',
  description: '服务端配置需要维护，当前请求没有执行完成。',
  retryable: false,
}

const RESPONSE_INVALID_COPY: SearchErrorCopy = {
  title: '检索结果无法读取',
  description: '上游返回的数据不符合当前契约，请稍后再试。',
}

const FALLBACK_COPY: SearchErrorCopy = {
  title: '本次检索未完成',
  description: '发生了未分类的服务错误，请稍后重试。',
}

const COPY_BY_CODE: Readonly<Partial<Record<string, SearchErrorCopy>>> = {
  validation_error: VALIDATION_COPY,

  request_timeout: TIMEOUT_COPY,
  embedding_timeout: TIMEOUT_COPY,
  qdrant_timeout: TIMEOUT_COPY,

  network_error: UNAVAILABLE_COPY,
  embedding_unavailable: UNAVAILABLE_COPY,
  qdrant_unavailable: UNAVAILABLE_COPY,
  qdrant_service_error: UNAVAILABLE_COPY,

  embedding_authentication_failed: CONFIGURATION_COPY,
  embedding_model_not_found: CONFIGURATION_COPY,
  qdrant_authentication_failed: CONFIGURATION_COPY,
  qdrant_target_missing: CONFIGURATION_COPY,
  qdrant_configuration_invalid: CONFIGURATION_COPY,
  search_runtime_unavailable: CONFIGURATION_COPY,

  embedding_response_invalid: RESPONSE_INVALID_COPY,
  qdrant_response_invalid: RESPONSE_INVALID_COPY,
  response_invalid: RESPONSE_INVALID_COPY,
}

// 走到这张表的只有「422 但 code 不在上表里」，也就是后端路由级脱敏返回的
// invalid_request。它同样是请求没通过校验，所以复用校验文案。
const COPY_BY_STATUS: Readonly<Partial<Record<number, SearchErrorCopy>>> = {
  422: VALIDATION_COPY,
}

export function presentSearchError(error: ApiError): SearchErrorPresentation {
  const copy = resolveErrorCopy(error, {
    byCode: COPY_BY_CODE,
    byStatus: COPY_BY_STATUS,
    fallback: FALLBACK_COPY,
  })

  return {
    title: copy.title,
    description: copy.description,
    retryable: copy.retryable ?? error.retryable,
  }
}
