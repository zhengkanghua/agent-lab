import type { DocumentPreviewDto, ReviewDetailDto } from './document-review'
import { newsKnowledgeBase } from './knowledge-bases.fixture'

export const reviewDocumentId = '41000000-0000-4000-8000-000000000001'
export const draftId = '41000000-0000-4000-8000-000000000002'
export const sourceId = '41000000-0000-4000-8000-000000000003'
export const versionId = '41000000-0000-4000-8000-000000000004'

export function reviewPreview(body = '备份保留 14 天。'): DocumentPreviewDto {
  return {
    document: {
      title: '维护手册',
      body: '# 维护手册\n\n## 备份\n\n' + body,
      text_format: 'markdown',
      parser: 'fixture-parser',
      issues: [],
      outline: [
        { id: 'h1', title: '维护手册', level: 1, parent_id: null },
        { id: 'h2', title: '备份', level: 2, parent_id: 'h1' },
      ],
      blocks: [
        {
          id: 'h1',
          kind: 'heading',
          text: '维护手册',
          heading_ids: [],
          level: 1,
          group_kind: 'group',
          enumerated: false,
        },
        {
          id: 'h2',
          kind: 'heading',
          text: '备份',
          heading_ids: ['h1'],
          level: 2,
          group_kind: 'group',
          enumerated: false,
        },
        {
          id: 'p1',
          kind: 'paragraph',
          text: body,
          heading_ids: ['h1', 'h2'],
          group_kind: 'group',
          enumerated: false,
        },
      ],
    },
    chunk_result: {
      specification: {
        algorithm: 'fixture-chunker',
        tokenizer: 'fixture-tokenizer',
        tokenizer_revision: 'fixture',
        max_tokens: 512,
      },
      chunks: [
        {
          sequence: 0,
          text: body,
          embedding_text: '维护手册\n备份\n' + body,
          token_count: 18,
          block_ids: ['p1'],
          heading_ids: ['h1', 'h2'],
          headings: ['维护手册', '备份'],
        },
      ],
      issues: [],
    },
  }
}

export function reviewDetail(): ReviewDetailDto {
  const summary = {
    document_id: reviewDocumentId,
    title: '维护手册',
    candidate_revision: 2,
    requires_review: true,
    preview_fingerprint: 'a'.repeat(64),
    error_code: null,
    issue_codes: [],
    source_stored: true,
    source_sha256: 'c'.repeat(64),
    created_at: '2026-09-10T08:00:00Z',
    updated_at: '2026-09-10T09:00:00Z',
  }
  const candidate = {
    ...summary,
    processing_id: draftId,
    source_kind: 'manual',
    state: 'review',
    text_format: 'markdown' as const,
    draft_text: reviewPreview().document.body,
    preview: reviewPreview(),
  }
  return {
    document: {
      document_id: reviewDocumentId,
      knowledge_base_id: newsKnowledgeBase.id,
      knowledge_base_name: newsKnowledgeBase.name,
      knowledge_base_active: true,
      title: '维护手册',
      source_kind: 'file',
      upload_filename: '维护手册.md',
      usage_status: 'active',
      revision: 1,
      management_revision: 10,
      current_version_id: versionId,
      latest_processing_id: sourceId,
      draft_processing_id: draftId,
      processing_state: 'review',
      error_code: null,
      deletion_pending: false,
      updated_at: '2026-09-10T09:00:00Z',
    },
    candidate,
    draft: { ...summary, processing_id: draftId, source_kind: 'manual', state: 'review' },
    latest_source: {
      ...summary,
      processing_id: sourceId,
      source_kind: 'file',
      state: 'adopted',
      requires_review: false,
    },
  }
}
