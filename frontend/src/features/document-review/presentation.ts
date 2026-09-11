import { ApiError } from '@/api/client'

export function reviewError(error: unknown): string {
  if (!(error instanceof ApiError)) return '操作未完成，请刷新核对当前状态。'
  if (['request_timeout', 'network_error', 'response_invalid'].includes(error.code))
    return '操作结果尚未确认，已尝试刷新核对状态；请核对后再操作，系统不会自动重复提交。'
  return error.detail
}

export function issueLabel(code: string): string {
  const labels: Record<string, string> = {
    document_title_empty: '缺少标题',
    document_body_empty: '没有有效正文',
    document_chunks_empty: '没有可用的 Chunk',
    document_parse_failed: '原件解析失败',
    document_chunking_failed: 'Chunk 生成失败',
    unsupported_content_block: '存在未支持的内容块',
    document_encoding_invalid: '原件不是有效的 UTF-8 文本',
    document_content_invalid: '正文包含不支持的字符',
    chunk_token_budget_exceeded: 'Chunk 超出长度预算',
    chunk_heading_budget_exceeded: '标题上下文超出长度预算',
    chunk_table_header_budget_exceeded: '表头超出长度预算',
    chunk_heading_context_lost: '部分标题上下文未保留',
    chunk_content_warning: '解析器提示可能存在内容丢弃',
    document_tokenizer_unavailable: '文本计数资源尚未就绪，请检查后端配置',
    document_tokenizer_mismatch: '文本计数资源与锁定版本不一致，请检查后端配置',
  }
  return labels[code] ?? '需要核对（' + code + '）'
}
