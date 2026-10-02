import { ref } from 'vue'
import { listKnowledgeBases, type KnowledgeBaseDto } from '@/api/knowledge-bases'

/* 维护清理范围的可选项。
 *
 * 为什么不复用 shared/composables/useKnowledgeBaseScope：那个给的是**启用**知识库
 * （检索范围只可能落在启用库上），而清理任务要能选到**已停用**库——历史范围里可能就指着一个
 * 已经停用的库，选项里没有它，用户就只能改选，等于逼他改掉一个本来正确的配置。
 * 两者的请求参数（includeDisabled）与错误文案都不同，硬合成一个反而要长出两个开关。
 *
 * 它存在的另一个理由：这件事原来写在 ScheduledJobsPage 里，页面直接 import @/api 并自己
 * 持请求状态，与「src/pages 只做路由级组合、不执行请求」的边界冲突。搬到这里之后页面只负责
 * 渲染与重试。
 */
export function useKnowledgeBaseOptions() {
  const options = ref<KnowledgeBaseDto[]>([])
  const error = ref('')

  async function load(): Promise<void> {
    try {
      options.value = await listKnowledgeBases(true)
      error.value = ''
    } catch {
      error.value = '知识库选项加载失败，清理范围暂不可选。'
    }
  }

  return { options, error, load }
}
