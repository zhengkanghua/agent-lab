import type { components } from './generated/openapi'
import { hasText, isRecord, isUuid } from './json-guards'

export type KnowledgeBaseSelection = components['schemas']['KnowledgeBaseSelection']
export type ResolvedKnowledgeBaseScope = components['schemas']['ResolvedKnowledgeBaseScope']

export function copySelection(selection: KnowledgeBaseSelection): KnowledgeBaseSelection {
  return selection.mode === 'all'
    ? { mode: 'all' }
    : { mode: 'selected', knowledge_base_ids: [...(selection.knowledge_base_ids ?? [])] }
}

export function isSelection(value: unknown): value is KnowledgeBaseSelection {
  if (!isRecord(value)) return false
  if (value.mode === 'all')
    return (
      value.knowledge_base_ids === undefined ||
      (Array.isArray(value.knowledge_base_ids) && !value.knowledge_base_ids.length)
    )
  return (
    value.mode === 'selected' &&
    Array.isArray(value.knowledge_base_ids) &&
    value.knowledge_base_ids.length > 0 &&
    value.knowledge_base_ids.every(isUuid)
  )
}

export function isResolvedScope(value: unknown): value is ResolvedKnowledgeBaseScope {
  return (
    isRecord(value) &&
    (value.mode === 'all' || value.mode === 'selected') &&
    Array.isArray(value.knowledge_bases) &&
    value.knowledge_bases.length > 0 &&
    value.knowledge_bases.every(
      (item) => isRecord(item) && isUuid(item.id) && hasText(item.name) && hasText(item.key),
    )
  )
}

export function scopeLabel(scope: ResolvedKnowledgeBaseScope): string {
  const names = scope.knowledge_bases.map((item) => item.name).join('、')
  return scope.mode === 'all' ? `所有知识库：${names}` : names
}
