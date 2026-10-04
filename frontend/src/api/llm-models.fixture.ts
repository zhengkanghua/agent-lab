import type { AvailableLlmModelDto, LlmModelDto } from './llm-models'

/** 页面与契约测试共用的合成 HTTP 契约样本。 */
export const defaultLlmModel: LlmModelDto = {
  id: '40000000-0000-4000-8000-000000000001',
  provider_id: '30000000-0000-4000-8000-000000000001',
  provider_name: '主中转站',
  provider_enabled: true,
  upstream_model_name: 'gpt-4o-mini',
  display_name: '快速模型',
  context_window: 32768,
  is_default: true,
  enabled: true,
  created_at: '2026-09-20T04:00:00Z',
  updated_at: '2026-09-20T04:00:00Z',
}

/** 没填展示名的那条：后台视图给 null，选择列表里已经回落成上游模型名。 */
export const namelessLlmModel: LlmModelDto = {
  ...defaultLlmModel,
  id: '40000000-0000-4000-8000-000000000002',
  upstream_model_name: 'qwen2.5:14b',
  display_name: null,
  context_window: 131072,
  is_default: false,
}

/** 所属渠道被停用：它自己还启用着，但对用户已经不可选。 */
export const orphanedLlmModel: LlmModelDto = {
  ...namelessLlmModel,
  id: '40000000-0000-4000-8000-000000000003',
  upstream_model_name: 'llama3.1:8b',
  provider_name: '本地 Ollama',
  provider_enabled: false,
  enabled: false,
}

export const availableLlmModels: AvailableLlmModelDto[] = [
  {
    id: '40000000-0000-4000-8000-000000000001',
    display_name: '快速模型',
    context_window: 32768,
    provider_name: '主中转站',
  },
  {
    id: '40000000-0000-4000-8000-000000000002',
    display_name: 'qwen2.5:14b',
    context_window: 131072,
    provider_name: '主中转站',
  },
]
