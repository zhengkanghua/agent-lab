<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { getDocumentOriginal } from '@/api/document-review'
import { isAbortError } from '@/api/client'
import BaseButton from '@/shared/ui/BaseButton.vue'
import BaseCallout from '@/shared/ui/BaseCallout.vue'
import { reviewError } from './presentation'

const props = defineProps<{ processingId: string; filename: string; stored: boolean }>()
const original = ref<Blob | null>(null)
const originalFilename = ref<string | null>(null)
const originalText = ref<string | null>(null)
const invalidEncoding = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
let controller: AbortController | undefined
let sequence = 0

async function load(): Promise<Blob | null> {
  if (original.value) return original.value
  controller?.abort()
  controller = new AbortController()
  const current = ++sequence
  loading.value = true
  error.value = null
  try {
    const file = await getDocumentOriginal(props.processingId, controller.signal)
    if (current !== sequence) return null
    original.value = file.blob
    originalFilename.value = file.filename
    return file.blob
  } catch (cause) {
    if (current === sequence && !isAbortError(cause)) error.value = reviewError(cause)
    return null
  } finally {
    if (current === sequence) loading.value = false
  }
}

async function view() {
  const currentId = props.processingId
  const blob = await load()
  if (!blob) return
  const bytes = await blob.arrayBuffer()
  if (currentId !== props.processingId) return
  try {
    originalText.value = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    invalidEncoding.value = true
    originalText.value = new TextDecoder('utf-8').decode(bytes)
  }
}

async function download() {
  const blob = await load()
  if (!blob) return
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = originalFilename.value || props.filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

watch(
  () => props.processingId,
  () => {
    sequence++
    controller?.abort()
    original.value = originalText.value = null
    originalFilename.value = null
    error.value = null
    loading.value = invalidEncoding.value = false
  },
)
onScopeDispose(() => {
  sequence++
  controller?.abort()
})
</script>

<template>
  <section class="document-original" aria-label="原始资料">
    <p class="original-note">原件保持上传时的字节；HTML 以文本展示。</p>
    <div class="original-actions">
      <BaseButton variant="outline" size="sm" :disabled="!stored || loading" @click="view"
        >查看原件文本</BaseButton
      >
      <BaseButton variant="ghost" size="sm" :disabled="!stored || loading" @click="download"
        >下载原件</BaseButton
      >
    </div>
    <p v-if="!stored" class="original-note">原件尚未确认保存，请先核对接收状态。</p>
    <p v-if="loading" role="status">正在读取原件…</p>
    <BaseCallout v-if="error" tone="danger" :description="error" />
    <p v-if="invalidEncoding" class="original-note">
      原件包含无效的 UTF-8 字节，文本视图使用替代字符；下载的文件保留原始字节。
    </p>
    <pre v-if="originalText !== null" class="original-text">{{ originalText }}</pre>
  </section>
</template>

<style scoped>
.document-original {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.original-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.original-note {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  line-height: 1.7;
}
.original-text {
  margin: 0;
  padding: 16px;
  max-height: 65vh;
  overflow-y: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  background: var(--surface-sunken);
  border-radius: var(--radius-sm);
  font-family: var(--mono-font);
  font-size: var(--fs-xs);
  line-height: 1.8;
}
</style>
