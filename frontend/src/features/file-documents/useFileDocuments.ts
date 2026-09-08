import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { ApiError } from '@/api/client'
import { listKnowledgeBases } from '@/api/knowledge-bases'
import {
  deleteFileDocument,
  listFileDocuments,
  retryFileDocument,
  saveFileDocument,
  type FileDocumentDto,
} from '@/api/file-documents'

export function useFileDocuments() {
  const offset = ref(0)
  const query = useQuery({
    queryKey: ['file-documents', offset],
    queryFn: ({ signal }) => listFileDocuments(offset.value, signal),
    retry: false,
    refetchInterval: (state) =>
      state.state.data?.items.some((item) =>
        ['pending', 'processing'].includes(item.processing_status),
      )
        ? 5000
        : false,
  })
  const directory = useQuery({
    queryKey: ['file-knowledge-bases'],
    queryFn: () => listKnowledgeBases(true),
    retry: false,
  })
  const knowledgeBases = computed(
    () => directory.data.value?.filter((item) => item.is_active) ?? [],
  )
  const busy = ref(false)
  const actionError = ref<string | null>(null)
  const feedback = ref<string | null>(null)
  const needsReselect = ref(false)

  async function refresh() {
    await Promise.all([query.refetch(), directory.refetch()])
  }

  async function save(
    file: File,
    knowledgeBaseId: string,
    target?: FileDocumentDto,
  ): Promise<boolean> {
    busy.value = true
    needsReselect.value = false
    actionError.value = feedback.value = null
    try {
      const saved = await saveFileDocument(file, knowledgeBaseId, target)
      feedback.value =
        target && saved.revision === target.revision
          ? '文件内容与索引信息没有变化，已保留原有处理状态。'
          : '文件已保存，正在等待建立索引；处理完成后即可检索。'
      offset.value = 0
      await query.refetch()
      return true
    } catch (error) {
      actionError.value = errorCopy(error, target ? '替换' : '上传')
      if (error instanceof ApiError && error.code === 'file_revision_conflict') {
        needsReselect.value = true
        await query.refetch()
      }
      return false
    } finally {
      busy.value = false
    }
  }

  async function retry(item: FileDocumentDto) {
    busy.value = true
    actionError.value = feedback.value = null
    try {
      await retryFileDocument(item)
      feedback.value = '已重新排队，等待索引执行端处理。'
      await query.refetch()
    } catch (error) {
      actionError.value = errorCopy(error, '重试')
    } finally {
      busy.value = false
    }
  }

  async function remove(item: FileDocumentDto): Promise<boolean> {
    busy.value = true
    needsReselect.value = false
    actionError.value = feedback.value = null
    try {
      await deleteFileDocument(item)
      feedback.value = '文档及其索引已删除。'
      await query.refetch()
      return true
    } catch (error) {
      actionError.value = errorCopy(error, '删除')
      needsReselect.value = error instanceof ApiError && error.code === 'file_revision_conflict'
      await query.refetch()
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    offset,
    items: computed(() => query.data.value?.items ?? []),
    hasMore: computed(() => query.data.value?.has_more ?? false),
    maxFileBytes: computed(() => query.data.value?.max_file_bytes),
    loading: query.isPending,
    loadError: computed(() => {
      const error = query.error.value
      if (!error) return null
      if (error instanceof ApiError && error.status === 404)
        return '文件管理服务尚未就绪，请联系管理员检查服务部署。'
      return '文件列表加载失败，请刷新后重试。'
    }),
    directoryError: computed(() =>
      directory.error.value ? '知识库目录加载失败，请重新加载。' : null,
    ),
    knowledgeBases,
    busy,
    feedback,
    actionError,
    needsReselect,
    refresh,
    save,
    retry,
    remove,
  }
}

function errorCopy(error: unknown, action: string): string {
  if (!(error instanceof ApiError)) return `${action}未完成，请刷新列表核对状态。`
  if (['request_timeout', 'network_error', 'response_invalid'].includes(error.code)) {
    return `${action}结果尚未确认，请刷新列表核对；再次新增上传会创建另一篇文档。`
  }
  if (error.code === 'knowledge_base_inactive') return '知识库已停用，请选择启用的知识库。'
  if (error.code === 'file_revision_conflict')
    return '文档刚被更新，列表已刷新。请重新选择文档并确认操作。'
  if (error.status === 413) return '文件超过允许的体积上限，请选择更小的文件。'
  return error.detail
}
