import type { LlmProviderDto } from './llm-providers'

/** 页面与契约测试共用的合成 HTTP 契约样本。 */
export const openaiProvider: LlmProviderDto = {
  id: '30000000-0000-4000-8000-000000000001',
  name: '主中转站',
  provider: 'openai_compatible',
  base_url: 'https://api.example.com/v1',
  enabled: true,
  credential_configured: true,
  created_at: '2026-09-20T04:00:00Z',
  updated_at: '2026-09-20T04:00:00Z',
}

/** 本地自托管那条路径：不需要凭据，也允许停用。 */
export const localProvider: LlmProviderDto = {
  ...openaiProvider,
  id: '30000000-0000-4000-8000-000000000002',
  name: '本地 Ollama',
  provider: 'ollama',
  base_url: 'http://127.0.0.1:11434',
  enabled: false,
  credential_configured: false,
}
