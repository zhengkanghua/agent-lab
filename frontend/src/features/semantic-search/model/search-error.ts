import { ApiError } from '../../../api/client'

export interface SearchErrorPresentation {
  title: string
  description: string
  retryable: boolean
}

const unavailableCodes = new Set([
  'network_error',
  'embedding_unavailable',
  'qdrant_unavailable',
  'qdrant_service_error',
])

const configurationCodes = new Set([
  'embedding_authentication_failed',
  'embedding_model_not_found',
  'qdrant_authentication_failed',
  'qdrant_target_missing',
  'qdrant_configuration_invalid',
  'search_runtime_unavailable',
])

export function presentSearchError(error: ApiError): SearchErrorPresentation {
  if (error.code === 'validation_error' || error.status === 422) {
    return {
      title: '检索内容未通过校验',
      description: '请检查输入是否为空或过长，然后重新搜索。',
      retryable: false,
    }
  }

  if (
    error.code === 'request_timeout' ||
    error.code === 'embedding_timeout' ||
    error.code === 'qdrant_timeout'
  ) {
    return {
      title: '检索等待时间过长',
      description: '上游服务没有及时响应，可以稍后重试本次搜索。',
      retryable: true,
    }
  }

  if (unavailableCodes.has(error.code)) {
    return {
      title: '检索服务暂时不可用',
      description: '新闻索引或语义服务当前无法连接，请稍后重试。',
      retryable: error.retryable,
    }
  }

  if (configurationCodes.has(error.code)) {
    return {
      title: '检索服务尚未就绪',
      description: '服务端配置需要维护，当前请求没有执行完成。',
      retryable: false,
    }
  }

  if (
    error.code === 'embedding_response_invalid' ||
    error.code === 'qdrant_response_invalid' ||
    error.code === 'response_invalid'
  ) {
    return {
      title: '检索结果无法读取',
      description: '上游返回的数据不符合当前契约，请稍后再试。',
      retryable: error.retryable,
    }
  }

  return {
    title: '本次检索未完成',
    description: '发生了未分类的服务错误，请稍后重试。',
    retryable: error.retryable,
  }
}
