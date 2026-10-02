import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { getDocumentVersion } from '@/api/document-review'

/**
 * 「已采用版本」的详情，给对照栏用。
 *
 * 从工作台组件里挪出来的：数据请求归 composable，组件只管编排。与 `useDocumentReview`
 * 同一条分层——那个管候选与人工草稿，这条管已采用的正式版本，两者的缓存键也不相干。
 *
 * `enabled` 由调用方给（对照栏切到「已采用版本」时才取）；`versionId` 为空时不发请求，
 * 缓存键里带上它，所以换了已采用版本会自然重取。
 */
export function useAdoptedVersion(
  documentId: MaybeRefOrGetter<string>,
  versionId: MaybeRefOrGetter<string | null | undefined>,
  enabled: MaybeRefOrGetter<boolean>,
) {
  return useQuery({
    queryKey: computed(() => ['document-version', toValue(documentId), toValue(versionId)]),
    queryFn: ({ signal }) => getDocumentVersion(toValue(documentId), toValue(versionId)!, signal),
    enabled: computed(() => toValue(enabled) && Boolean(toValue(versionId))),
    retry: false,
  })
}
