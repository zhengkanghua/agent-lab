import type { LlmProviderKind } from '@/api/llm-providers'

/**
 * 接入类型在界面上的叫法。
 *
 * 取值与顺序来自后端契约（`LlmProvider`），这里只补中文名。`satisfies` 让契约上新增第三种
 * 接入类型时在类型检查里报错，而不是在界面上静默显示成英文枚举值。
 */
export const PROVIDER_KIND_LABELS = {
  openai_compatible: 'OpenAI 兼容',
  ollama: 'Ollama (本地)',
} satisfies Record<LlmProviderKind, string>
