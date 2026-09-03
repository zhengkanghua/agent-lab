import { describe, expect, it } from 'vitest'
import {
  buildParams,
  validateCronShape,
  validateKey,
  validateParams,
  type JobFormValues,
} from '../model/job-validation'

function baseValues(overrides: Partial<JobFormValues> = {}): JobFormValues {
  return {
    key: 'freshrss-sync',
    taskType: 'freshrss_sync',
    cronExpr: '*/10 * * * *',
    limitPerSource: 2,
    batchSize: 20,
    staleAfterMinutes: 60,
    enabled: true,
    ...overrides,
  }
}

describe('validateKey', () => {
  it('accepts lowercase slugs with dashes', () => {
    expect(validateKey('freshrss-sync')).toBe('')
    expect(validateKey('abc1')).toBe('')
  })

  it('rejects empty, uppercase, and edge dashes', () => {
    expect(validateKey('')).not.toBe('')
    expect(validateKey('FreshRSS')).not.toBe('')
    expect(validateKey('-lead')).not.toBe('')
    expect(validateKey('trail-')).not.toBe('')
    expect(validateKey('双横--线')).not.toBe('')
  })
})

describe('validateCronShape', () => {
  it('accepts 5 fields and rejects anything else', () => {
    expect(validateCronShape('*/10 * * * *')).toBe('')
    expect(validateCronShape('0 9 * * 1-5')).toBe('')
    expect(validateCronShape('* * * *')).not.toBe('')
    expect(validateCronShape('九点')).not.toBe('')
  })
})

describe('validateParams', () => {
  it('passes a well-formed freshrss_sync form', () => {
    expect(validateParams(baseValues())).toEqual({})
  })

  it('bounds each type’s parameters', () => {
    expect(validateParams(baseValues({ limitPerSource: 0 })).limitPerSource).toBeDefined()
    expect(validateParams(baseValues({ limitPerSource: 101 })).limitPerSource).toBeDefined()

    const indexForm = baseValues({ taskType: 'index_pending', key: 'index-pending' })
    expect(validateParams(indexForm)).toEqual({})
    expect(
      validateParams(baseValues({ taskType: 'index_pending', batchSize: 0 })).batchSize,
    ).toBeDefined()
    expect(
      validateParams(baseValues({ taskType: 'index_pending', staleAfterMinutes: 0 }))
        .staleAfterMinutes,
    ).toBeDefined()
  })

  it('rejects unknown task types and bad cron shapes', () => {
    expect(validateParams(baseValues({ taskType: 'mystery' })).taskType).toBeDefined()
    expect(validateParams(baseValues({ cronExpr: 'bad cron' })).cron).toBeDefined()
  })
})

describe('buildParams', () => {
  it('emits only the fields of the selected type', () => {
    expect(buildParams(baseValues())).toEqual({ limit_per_source: 2 })
    expect(
      buildParams(baseValues({ taskType: 'index_pending', batchSize: 7, staleAfterMinutes: 30 })),
    ).toEqual({ batch_size: 7, stale_after_minutes: 30 })
  })
})
