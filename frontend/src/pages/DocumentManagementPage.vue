<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { isUuid } from '@/api/json-guards'
import { DocumentReviewDirectory, DocumentReviewWorkbench } from '@/features/document-review'

const route = useRoute()
const router = useRouter()
const queryClient = useQueryClient()
const documentId = computed(() => (isUuid(route.query.document) ? route.query.document : undefined))
function close() {
  void router.push({ name: 'admin', params: { section: 'documents' } })
}
function removed() {
  void queryClient.invalidateQueries({ queryKey: ['managed-documents'] })
  void queryClient.invalidateQueries({ queryKey: ['file-documents'] })
  close()
}
</script>

<template>
  <DocumentReviewWorkbench
    v-if="documentId"
    :key="documentId"
    :document-id="documentId"
    @close="close"
    @removed="removed"
  />
  <DocumentReviewDirectory
    v-else
    @open="
      router.push({ name: 'admin', params: { section: 'documents' }, query: { document: $event } })
    "
  />
</template>
