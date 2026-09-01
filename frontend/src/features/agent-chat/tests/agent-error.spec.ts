import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { presentAgentError } from '../model/agent-error'

function apiError(code: string, status = 0, retryable = false): ApiError {
  return new ApiError({ message: 'detail', code, status, retryable })
}

describe('presentAgentError', () => {
  it('按错误码给出该做什么', () => {
    expect(presentAgentError(apiError('llm_rate_limited', 429, true))).toEqual({
      title: '模型调用过于频繁',
      description: expect.stringContaining('等一会儿'),
      retryable: true,
    })
  })

  it('配置类错误钉成不可重试，覆盖后端给的 retryable', () => {
    // 重发同一个问题不会让服务端的模型配置变好，给出重试按钮只是让用户白试一次。
    expect(presentAgentError(apiError('llm_authentication_failed', 503, true)).retryable).toBe(
      false,
    )
  })

  it('没有钉死重试语义的文案沿用后端给的值', () => {
    expect(presentAgentError(apiError('llm_response_invalid', 502, true)).retryable).toBe(true)
    expect(presentAgentError(apiError('llm_response_invalid', 502, false)).retryable).toBe(false)
  })

  it('会话记忆中途断连是可重试的，不能和「服务端没配好」共用文案', () => {
    // 这两个码都来自 checkpointer，但要用户做的事相反：连接中断重发一次就好，
    // 配置没做好重发一百次也一样。共用 CONFIGURATION_COPY 会把「重发即可」的故障
    // 说成「等管理员」，用户于是不会去点重试。
    const lost = presentAgentError(apiError('agent_checkpointer_connection_lost', 503, true))

    expect(lost.title).toBe('会话记忆连接中断')
    expect(lost.retryable).toBe(true)
    expect(presentAgentError(apiError('agent_checkpointer_unavailable', 503, true)).retryable).toBe(
      false,
    )
  })

  it('code 优先于 status', () => {
    // 422 表里是校验文案，但带上 llm_request_rejected 就该说「换个说法」。
    expect(presentAgentError(apiError('llm_request_rejected', 422)).title).toBe(
      '模型拒绝了这次请求',
    )
  })

  it('只有 status 能判定时退到 status 表', () => {
    expect(presentAgentError(apiError('unknown_error', 403)).title).toBe('没有使用 Agent 的权限')
    expect(presentAgentError(apiError('unknown_error', 422)).title).toBe('提问未通过校验')
  })

  it('未登记的错误码退到兜底，不崩', () => {
    expect(presentAgentError(apiError('some_new_backend_code', 500, true))).toEqual({
      title: '本轮对话未完成',
      description: expect.any(String),
      retryable: true,
    })
  })

  it('前端自造的传输层错误码也有文案', () => {
    expect(presentAgentError(apiError('request_timeout', 0, true)).title).toBe('模型响应超时')
    expect(presentAgentError(apiError('network_error', 0, true)).title).toBe('模型服务暂时不可用')
  })

  it('会话打不开时的文案不透露它是否存在，也不说「这是别人的」', () => {
    // 后端把「不存在」和「不属于你」合并成同一个 404，就是为了不让人靠差异枚举会话 id。
    // 文案里写「这是别人的会话」等于把后端刻意藏起来的信息又说出去；而当会话其实是被自己
    // 在另一个标签页删掉时，那句话还是错的。
    const copy = presentAgentError(apiError('agent_thread_not_found', 404, false))

    expect(copy.title).toBe('会话不存在或已被删除')
    expect(copy.retryable).toBe(false)
    for (const forbidden of ['别人', '他人', '无权', '权限']) {
      expect(`${copy.title}${copy.description}`).not.toContain(forbidden)
    }
  })

  it('会话记录存储不可用是可重试的，且与模型不可用分开表述', () => {
    const copy = presentAgentError(apiError('agent_thread_database_unavailable', 503, true))

    expect(copy.title).toBe('会话记录暂时读不出来')
    expect(copy.retryable).toBe(true)
    // 与模型不可用区分：用户能做的事不同，混成一句会让人以为模型也挂了。
    expect(copy.title).not.toBe(presentAgentError(apiError('llm_unavailable', 503, true)).title)
  })
})
