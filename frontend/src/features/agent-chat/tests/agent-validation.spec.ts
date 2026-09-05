import { describe, expect, it } from 'vitest'
import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'
import {
  MAX_MESSAGE_CHARACTERS,
  validateMessage,
  validateSystemPrompt,
} from '../model/agent-validation'

describe('agent-validation', () => {
  it('两个上界与后端 agent/limits.py 对齐', () => {
    expect(MAX_MESSAGE_CHARACTERS).toBe(4000)
    expect(MAX_SYSTEM_PROMPT_CHARACTERS).toBe(4000)
  })

  it('空白提问不通过', () => {
    expect(validateMessage('')).not.toBeNull()
    expect(validateMessage('   \n ')).not.toBeNull()
  })

  it('刚好到上界的提问可以发', () => {
    expect(validateMessage('问'.repeat(MAX_MESSAGE_CHARACTERS))).toBeNull()
    expect(validateMessage('问'.repeat(MAX_MESSAGE_CHARACTERS + 1))).not.toBeNull()
  })

  it('系统提示词留空不算错', () => {
    // 清空输入框的语义是「用服务端默认的那份」，后端的 field_validator 也这么处理。
    expect(validateSystemPrompt('')).toBeNull()
    expect(validateSystemPrompt('  ')).toBeNull()
  })

  it('系统提示词超长报错', () => {
    expect(validateSystemPrompt('字'.repeat(MAX_SYSTEM_PROMPT_CHARACTERS))).toBeNull()
    expect(validateSystemPrompt('字'.repeat(MAX_SYSTEM_PROMPT_CHARACTERS + 1))).not.toBeNull()
  })
})
