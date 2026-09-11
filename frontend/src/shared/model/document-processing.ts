import { ApiError } from '@/api/client'

export const processingStates = [
  ['review', '待审核'],
  ['failed', '解析失败'],
  ['adoption_failed', '采用失败'],
  ['receiving_failed', '原件保存失败'],
  ['received', '原件待核对'],
  ['stored', '等待来源确认'],
  ['draft', '人工草稿'],
  ['pending', '等待解析'],
  ['processing', '正在解析'],
  ['ready', '等待自动采用'],
  ['adopting', '等待建立新索引'],
  ['indexing', '正在建立新索引'],
  ['adopted', '已采用'],
  ['rejected', '已拒绝'],
] as const

export function processingLabel(state: string | null | undefined): string {
  const maintenance: Record<string, string> = {
    rebuilding: '正在重建索引',
    publishing: '正在发布索引',
    rebuilt: '重建完成',
  }
  return (
    processingStates.find(([value]) => value === state)?.[1] ??
    maintenance[state ?? ''] ??
    (state ? '待核对：' + state : '尚未处理')
  )
}

export function isProcessing(state: string | null | undefined): boolean {
  return ['pending', 'processing', 'ready', 'adopting', 'indexing', 'rebuilding'].includes(
    state ?? '',
  )
}

export function usageLabel(status: string, hasVersion: boolean, active = true): string {
  if (status === 'deleting') return '删除未完成'
  if (status === 'rejected') return '已停止使用'
  if (!active) return '知识库已停用'
  return hasVersion && status === 'active' ? '已采用版本可用' : '尚未采用'
}

export function isDocumentConflict(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    [
      'document_processing_conflict',
      'document_adoption_conflict',
      'file_revision_conflict',
    ].includes(error.code)
  )
}
