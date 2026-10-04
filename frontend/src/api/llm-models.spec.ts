import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createLlmModel,
  listAvailableLlmModels,
  listLlmModels,
  updateLlmModel,
} from './llm-models'
import {
  availableLlmModels,
  defaultLlmModel,
  namelessLlmModel,
  orphanedLlmModel,
} from './llm-models.fixture'

afterEach(() => vi.unstubAllGlobals())

describe('llm model API', () => {
  it('reads the management list from the same-origin contract', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json([defaultLlmModel, namelessLlmModel, orphanedLlmModel]))
    vi.stubGlobal('fetch', fetchMock)

    expect(await listLlmModels()).toEqual([
      defaultLlmModel,
      namelessLlmModel,
      orphanedLlmModel,
    ])
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/llm-models')
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: 'same-origin',
      method: 'GET',
    })
  })

  it('reads the selection list, where the display name has already been resolved', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(availableLlmModels))
    vi.stubGlobal('fetch', fetchMock)

    expect(await listAvailableLlmModels()).toEqual(availableLlmModels)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/llm-models/available')
  })

  it('sends only the fields the command owns', async () => {    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(defaultLlmModel))
      .mockResolvedValueOnce(Response.json(namelessLlmModel))
    vi.stubGlobal('fetch', fetchMock)

    await createLlmModel({
      provider_id: defaultLlmModel.provider_id,
      upstream_model_name: 'gpt-4o-mini',
      display_name: '快速模型',
      context_window: 32768,
      is_default: false,
      enabled: true,
    })
    // 编辑不带 is_default：默认标记只能由「设为默认」那条命令改，编辑不该顺手动它。
    await updateLlmModel(defaultLlmModel.id, {
      upstream_model_name: 'gpt-4o-mini',
      display_name: '',
      context_window: 65536,
      enabled: true,
    })

    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({
      provider_id: defaultLlmModel.provider_id,
      upstream_model_name: 'gpt-4o-mini',
      display_name: '快速模型',
      context_window: 32768,
      is_default: false,
      enabled: true,
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/llm-models/${defaultLlmModel.id}`)
    expect(fetchMock.mock.calls[1]?.[1].method).toBe('PATCH')
    // 空串表示「改成没有展示名」，字段必须在场——省掉它就成了「不修改」。
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual({
      upstream_model_name: 'gpt-4o-mini',
      display_name: '',
      context_window: 65536,
      enabled: true,
    })
  })

  it.each([
    null,
    {},
    [{ ...defaultLlmModel, id: 'bad-id' }],
    [{ ...defaultLlmModel, upstream_model_name: '' }],
    [{ ...defaultLlmModel, context_window: 0 }],
    [{ ...defaultLlmModel, context_window: '32768' }],
    [{ ...defaultLlmModel, enabled: 'true' }],
    [{ ...defaultLlmModel, provider_enabled: undefined }],
    [{ ...defaultLlmModel, updated_at: 'not-a-date' }],
  ])('rejects malformed management data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(listLlmModels()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it.each([
    null,
    [{}],
    // 选择列表里的展示名是服务端落过回落的，空串说明那份回落没做，不能当成一条可选项渲染
    [{ ...availableLlmModels[0], display_name: '' }],
    [{ ...availableLlmModels[0], context_window: 0 }],
    [{ ...availableLlmModels[0], provider_name: '' }],
  ])('rejects malformed selection data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(listAvailableLlmModels()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('preserves the backend failure contract', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json(
          {
            code: 'llm_model_default_conflict',
            detail: '另一个请求刚刚改过默认模型，请刷新列表后重试。',
            retryable: false,
          },
          { status: 409 },
        ),
      ),
    )
    await expect(updateLlmModel(defaultLlmModel.id, { is_default: true })).rejects.toMatchObject({
      status: 409,
      code: 'llm_model_default_conflict',
      retryable: false,
    })
  })
})
