import type { ApiError } from '@/api/client'
import { resolveErrorCopy, type ErrorCopy } from '@/api/error-copy'

export interface AgentErrorPresentation extends ErrorCopy {
  retryable: boolean
}

/** 文案表里的一条。省略 retryable 表示沿用后端给的重试语义。 */
interface AgentErrorCopy extends ErrorCopy {
  retryable?: boolean
}

const VALIDATION_COPY: AgentErrorCopy = {
  title: '提问未通过校验',
  description: '请检查提问是否为空或过长，然后重新发送。',
  retryable: false,
}

const TIMEOUT_COPY: AgentErrorCopy = {
  title: '模型响应超时',
  description: '模型没有在预期时间内给出回答，可以稍后重发本轮提问。',
  retryable: true,
}

const RATE_LIMITED_COPY: AgentErrorCopy = {
  title: '模型调用过于频繁',
  description: '上游已限流，等一会儿再发送同一个问题即可。',
  retryable: true,
}

const UNAVAILABLE_COPY: AgentErrorCopy = {
  title: '模型服务暂时不可用',
  description: '当前连不上模型服务，请稍后重试。',
  retryable: true,
}

const CONFIGURATION_COPY: AgentErrorCopy = {
  title: 'Agent 尚未就绪',
  description: '服务端的模型配置需要维护，本轮提问没有执行完成。',
  retryable: false,
}

// 和 CONFIGURATION_COPY 的区别：那个是服务端还没配好，重试无用；这个是会话记忆的连接在
// 中途断了，重发同一个问题通常就成功。所以不写 retryable，沿用后端给的 true。
const MEMORY_CONNECTION_LOST_COPY: AgentErrorCopy = {
  title: '会话记忆连接中断',
  description: '本轮提问没有存进会话历史，重新发送一次即可。',
}

const REJECTED_COPY: AgentErrorCopy = {
  title: '模型拒绝了这次请求',
  description: '可能是提问或自定义提示词触发了上游限制，换个说法再试。',
  retryable: false,
}

// 和 REJECTED_COPY 的区别：那个是「这句话不行、换个说法」，这个是上游 403 拒绝了整个客户端，
// 换问法没用，得服务端去查。文案因此不引导用户重写提问。
const BLOCKED_COPY: AgentErrorCopy = {
  title: '模型服务拒绝了本次请求',
  description: '上游判定这次调用不被允许，重试和换问法都无效，请联系管理员排查。',
  retryable: false,
}

const RESPONSE_INVALID_COPY: AgentErrorCopy = {
  title: '模型回答无法读取',
  description: '返回的数据不符合当前契约，请稍后重试。',
}

const PERMISSION_COPY: AgentErrorCopy = {
  title: '没有使用 Agent 的权限',
  description: 'Agent 对话目前只对管理员开放，请联系管理员开通。',
  retryable: false,
}

const FALLBACK_COPY: AgentErrorCopy = {
  title: '本轮对话未完成',
  description: '发生了未分类的服务错误，请稍后重试。',
}

// 错误码来自后端 api/error_contract.py 的 AGENT_CHAT_ERROR_RULES；新增码时两边一起改，
// 漏了这边只会退到兜底文案，不会崩，但用户就看不到「该怎么办」了。
const COPY_BY_CODE: Readonly<Partial<Record<string, AgentErrorCopy>>> = {
  validation_error: VALIDATION_COPY,

  request_timeout: TIMEOUT_COPY,
  llm_timeout: TIMEOUT_COPY,

  llm_rate_limited: RATE_LIMITED_COPY,

  network_error: UNAVAILABLE_COPY,
  llm_unavailable: UNAVAILABLE_COPY,
  llm_service_error: UNAVAILABLE_COPY,
  agent_tool_database_unavailable: UNAVAILABLE_COPY,

  agent_checkpointer_connection_lost: MEMORY_CONNECTION_LOST_COPY,

  agent_runtime_unavailable: CONFIGURATION_COPY,
  agent_checkpointer_unavailable: CONFIGURATION_COPY,
  llm_authentication_failed: CONFIGURATION_COPY,
  llm_model_not_found: CONFIGURATION_COPY,

  llm_request_rejected: REJECTED_COPY,
  llm_request_blocked: BLOCKED_COPY,

  llm_response_invalid: RESPONSE_INVALID_COPY,
  response_invalid: RESPONSE_INVALID_COPY,

  permission_denied: PERMISSION_COPY,

  agent_internal_error: FALLBACK_COPY,
  agent_tool_failed: FALLBACK_COPY,
}

// 只会走到「422 但响应体没带 code」这一种情况，也就是路由级脱敏后的校验失败。
const COPY_BY_STATUS: Readonly<Partial<Record<number, AgentErrorCopy>>> = {
  403: PERMISSION_COPY,
  422: VALIDATION_COPY,
}

export function presentAgentError(error: ApiError): AgentErrorPresentation {
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
