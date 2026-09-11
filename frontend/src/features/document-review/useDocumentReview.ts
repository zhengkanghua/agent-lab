import { computed, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { isAbortError } from '@/api/client'
import {
  adoptDocumentCandidate,
  deleteManagedDocument,
  getDocumentReview,
  previewDocumentDraft,
  rejectDocumentCandidate,
  retryDocumentCandidate,
  saveDocumentDraft,
  startDocumentReview,
  useLatestDocumentSource,
  type ProcessingReceiptDto,
  type ReviewDetailDto,
} from '@/api/document-review'
import { isProcessing, isDocumentConflict } from '@/shared/model/document-processing'
import { reviewError } from './presentation'

interface EditorBase {
  processingId: string
  managementRevision: number
  candidateRevision: number
  title: string
  text: string
}

/** 保存的草稿与本地编辑分开；后台轮询只能更新状态，不能覆盖尚未保存的文字。 */
export function useDocumentReview(documentId: MaybeRefOrGetter<string>) {
  const detail = ref<ReviewDetailDto | null>(null)
  const title = ref('')
  const text = ref('')
  const base = ref<EditorBase | null>(null)
  const loading = ref(false)
  const busy = ref(false)
  const error = ref<string | null>(null)
  const feedback = ref<string | null>(null)
  const conflict = ref(false)
  const stale = ref(false)
  const historyRevision = ref(0)
  let processingId: string | undefined
  let controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let readSequence = 0
  let disposed = false

  const dirty = computed(
    () => !!base.value && (title.value !== base.value.title || text.value !== base.value.text),
  )
  const frozen = computed(() =>
    ['adopting', 'indexing'].includes(detail.value?.candidate.state ?? ''),
  )
  const editable = computed(() => {
    const current = detail.value
    return (
      !!current &&
      current.candidate.source_kind === 'manual' &&
      current.document.draft_processing_id === current.candidate.processing_id &&
      current.document.knowledge_base_active &&
      !current.document.deletion_pending &&
      !frozen.value &&
      !busy.value &&
      !stale.value &&
      !conflict.value
    )
  })
  const canAdopt = computed(() => {
    const current = detail.value
    return (
      !!current &&
      !!current.candidate.preview_fingerprint &&
      !!current.candidate.preview?.chunk_result.chunks.length &&
      !!current.candidate.preview.document.title.trim() &&
      current.candidate.preview.chunk_result.chunks.every(
        (chunk) =>
          chunk.token_count <= current.candidate.preview!.chunk_result.specification.max_tokens,
      ) &&
      ['review', 'ready'].includes(current.candidate.state) &&
      current.document.knowledge_base_active &&
      !current.document.deletion_pending &&
      !dirty.value &&
      !busy.value &&
      !stale.value &&
      !conflict.value
    )
  })

  function stopPolling() {
    clearTimeout(timer)
    timer = undefined
  }

  function adoptEditor(current: ReviewDetailDto) {
    const candidate = current.candidate
    title.value = candidate.title
    text.value = candidate.draft_text ?? candidate.preview?.document.body ?? ''
    base.value = {
      processingId: candidate.processing_id,
      managementRevision: current.document.management_revision,
      candidateRevision: candidate.candidate_revision,
      title: title.value,
      text: text.value,
    }
    conflict.value = false
  }

  function schedulePolling() {
    stopPolling()
    const current = detail.value
    if (
      !disposed &&
      current &&
      !current.document.deletion_pending &&
      [current.candidate.state, current.latest_source?.state, current.draft?.state].some(
        isProcessing,
      )
    ) {
      timer = setTimeout(() => {
        void refresh()
      }, 5000)
    }
  }

  async function refresh(replaceEditor = false): Promise<boolean> {
    stopPolling()
    controller?.abort()
    const request = new AbortController()
    controller = request
    const sequence = ++readSequence
    const id = toValue(documentId)
    loading.value = true
    try {
      const next = await getDocumentReview(id, processingId, request.signal)
      if (disposed || sequence !== readSequence) return false
      if (replaceEditor || !base.value || (!dirty.value && !conflict.value)) {
        adoptEditor(next)
      } else if (
        base.value.processingId !== next.candidate.processing_id ||
        base.value.managementRevision !== next.document.management_revision ||
        base.value.candidateRevision !== next.candidate.candidate_revision
      ) {
        conflict.value = true
      }
      detail.value = next
      stale.value = false
      error.value = null
      schedulePolling()
      return true
    } catch (cause) {
      if (!disposed && sequence === readSequence && !isAbortError(cause)) {
        error.value = reviewError(cause)
        stale.value = true
      }
      return false
    } finally {
      if (sequence === readSequence) loading.value = false
    }
  }

  function expected(current: ReviewDetailDto) {
    return {
      management_revision: base.value?.managementRevision ?? current.document.management_revision,
      candidate_revision: base.value?.candidateRevision ?? current.candidate.candidate_revision,
    }
  }

  async function perform(
    action: (current: ReviewDetailDto) => Promise<ProcessingReceiptDto>,
    message: string,
    allowConflict = false,
  ): Promise<boolean> {
    const current = detail.value
    if (!current || busy.value || stale.value || (conflict.value && !allowConflict)) return false
    const id = toValue(documentId)
    busy.value = true
    error.value = feedback.value = null
    stopPolling()
    // 避免命令期间在途的旧读取把确认目标换掉。
    controller?.abort()
    readSequence++
    loading.value = false
    try {
      const receipt = await action(current)
      if (disposed || id !== toValue(documentId)) return false
      processingId = receipt.processing_id
      historyRevision.value++
      const loaded = await refresh(true)
      feedback.value = message
      return loaded
    } catch (cause) {
      if (disposed || id !== toValue(documentId)) return false
      const message = reviewError(cause)
      if (isDocumentConflict(cause)) conflict.value = true
      // 超时或冲突只读取结果，不自动重放命令。
      await refresh()
      error.value = message
      return false
    } finally {
      if (!disposed && id === toValue(documentId)) busy.value = false
    }
  }

  function start() {
    return perform(
      (current) =>
        startDocumentReview(
          current.document.document_id,
          current.document.management_revision,
          current.candidate.processing_id,
        ),
      '人工草稿已准备好；编辑和预览不会改变已采用版本。',
    )
  }

  function useLatest() {
    return perform(
      (current) =>
        useLatestDocumentSource(current.document.document_id, current.document.management_revision),
      '已换用最新来源，请检查预览后决定是否采用。',
    )
  }

  async function save(): Promise<boolean> {
    if (!editable.value) return false
    if (!dirty.value) return true
    const savedTitle = title.value
    const savedText = text.value
    return perform(
      (current) =>
        saveDocumentDraft(current.candidate.processing_id, {
          ...expected(current),
          title: savedTitle,
          text: savedText,
        }),
      '草稿已保存，原预览已失效；请重新生成预览。',
    )
  }

  async function preview() {
    if (!(await save())) return false
    return perform(
      (current) => previewDocumentDraft(current.candidate.processing_id, expected(current)),
      '预览任务已保存，后台正在生成结构和 Chunk。',
    )
  }

  function adopt(conclusion: string) {
    if (!canAdopt.value) return Promise.resolve(false)
    return perform(
      (current) =>
        adoptDocumentCandidate(current.candidate.processing_id, {
          ...expected(current),
          fingerprint: current.candidate.preview_fingerprint!,
          conclusion: conclusion || null,
        }),
      '采用已受理，新索引准备成功后才切换；失败时保留旧版本。',
    )
  }

  function reject(conclusion: string) {
    return perform(
      (current) =>
        rejectDocumentCandidate(current.candidate.processing_id, {
          ...expected(current),
          conclusion: conclusion || null,
        }),
      '已停止整篇文档的后续使用，原件和管理记录仍保留。',
    )
  }

  function retry() {
    return perform(
      (current) => retryDocumentCandidate(current.candidate.processing_id, expected(current)),
      '已提交核对或重试，请查看处理状态。',
    )
  }

  /** 冲突后的覆盖必须由用户明确选择；先取得最新草稿，再保留用户自己的编辑。 */
  async function keepLocalEdits() {
    const localTitle = title.value
    const localText = text.value
    const accepted = await perform(
      (current) =>
        startDocumentReview(
          current.document.document_id,
          current.document.management_revision,
          current.candidate.processing_id,
        ),
      '已保留本地编辑，请对照最新服务端内容后保存。',
      true,
    )
    if (accepted) {
      title.value = localTitle
      text.value = localText
    }
  }

  function discardLocalEdits() {
    if (detail.value) adoptEditor(detail.value)
    error.value = null
  }

  function confirmLeave(): boolean {
    return !dirty.value || window.confirm('有尚未保存的正文修改。确认放弃本地修改并离开？')
  }

  async function selectCandidate(id: string) {
    if (busy.value || !confirmLeave()) return
    processingId = id
    await refresh(true)
  }

  async function remove(): Promise<boolean> {
    if (!detail.value || busy.value || stale.value || conflict.value) return false
    busy.value = true
    const id = toValue(documentId)
    stopPolling()
    try {
      await deleteManagedDocument(detail.value.document)
      if (disposed || id !== toValue(documentId)) return false
      base.value = null
      return true
    } catch (cause) {
      if (disposed || id !== toValue(documentId)) return false
      const message = reviewError(cause)
      await refresh()
      error.value = message
      return false
    } finally {
      if (!disposed && id === toValue(documentId)) busy.value = false
    }
  }

  watch(
    () => toValue(documentId),
    () => {
      processingId = undefined
      detail.value = base.value = null
      title.value = text.value = ''
      error.value = feedback.value = null
      conflict.value = stale.value = busy.value = false
      void refresh(true)
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    disposed = true
    readSequence++
    stopPolling()
    controller?.abort()
  })

  return {
    detail,
    title,
    text,
    loading,
    busy,
    error,
    feedback,
    conflict,
    stale,
    dirty,
    frozen,
    editable,
    canAdopt,
    historyRevision,
    refresh,
    start,
    useLatest,
    save,
    preview,
    adopt,
    reject,
    retry,
    remove,
    selectCandidate,
    keepLocalEdits,
    discardLocalEdits,
    confirmLeave,
  }
}
