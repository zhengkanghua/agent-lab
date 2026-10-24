// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { MAX_MESSAGE_CHARACTERS, validateMessage } from '../model/agent-validation'

describe('agent-validation', () => {
  it('提问上界与后端 agent/limits.py 对齐', () => {
    expect(MAX_MESSAGE_CHARACTERS).toBe(4000)
  })

  it('空白提问不通过', () => {
    expect(validateMessage('')).not.toBeNull()
    expect(validateMessage('   \n ')).not.toBeNull()
  })

  it('刚好到上界的提问可以发', () => {
    expect(validateMessage('问'.repeat(MAX_MESSAGE_CHARACTERS))).toBeNull()
    expect(validateMessage('问'.repeat(MAX_MESSAGE_CHARACTERS + 1))).not.toBeNull()
  })
})
