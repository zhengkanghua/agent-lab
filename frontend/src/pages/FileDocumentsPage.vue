<script setup lang="ts">
import { FileDocumentDirectory } from '@/features/file-documents'
import { useDocumentReader } from '@/shared/composables/useDocumentReader'
import DocumentReader from '@/shared/ui/DocumentReader.vue'
import type { FileDocumentDto } from '@/api/file-documents'

const reader = useDocumentReader()

function readFile(item: FileDocumentDto, trigger: HTMLElement): void {
  if (!item.content_hash || !item.current_version_id || item.usage_status !== 'active') return
  void reader.open(
    {
      documentId: item.document_id,
      knowledgeBaseId: item.knowledge_base_id,
      knowledgeBaseName: item.knowledge_base_name,
      uploadFilename: item.upload_filename,
      contentHash: item.content_hash,
      title: item.title,
      url: null,
      sourceName: null,
      publishedAt: null,
      authors: [],
      labels: [],
    },
    trigger,
  )
}
</script>

<template>
  <FileDocumentDirectory @read-document="readFile" />
  <DocumentReader
    :open="reader.isOpen.value"
    :result="reader.selectedResult.value"
    :detail="reader.detail.value"
    :loading="reader.isLoading.value"
    :error="reader.error.value"
    :hash-mismatch="reader.contentHashMismatch.value"
    @close="reader.close"
    @closed="reader.restoreFocus"
    @retry="reader.retry"
  />
</template>
