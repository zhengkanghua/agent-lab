import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'
import { isAbortError } from '@/api/client'
import { validateCron } from '@/api/scheduled-jobs'
import { formatBeijingTime } from '../model/job-copy'
import { presentJobError } from '../model/job-error'
import { validateCronShape } from '../model/job-validation'

export type CronPreviewState = 'idle' | 'invalid' | 'checking' | 'valid'

/**
 * cron 输入的防抖预览（Q4 共识）：输入停 300ms 后调一次后端 validate-cron，
 * 合法时给出未来 3 次执行时间（北京时间），非法时给出可读原因。
 *
 * 客户端只做「5 段」形状检查（零成本、零延迟）；语义对不对以服务端为准——
 * cron 的合法性规则（区间、步进、星期字段）不值得在前端复刻一遍。
 */
export function useCronPreview(cronExpr: Ref<string>, debounceMs = 300) {
  const state = ref<CronPreviewState>('idle')
  const previewTimes = ref<string[]>([])
  const failureMessage = ref('')

  let timer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null
  let requestSeq = 0

  const shapeMessage = computed(() => validateCronShape(cronExpr.value))

  const message = computed(() => {
    if (state.value === 'invalid') return failureMessage.value
    return ''
  })

  /** 提交闸门：形状合法且服务端确认通过才算就绪。 */
  const canSubmit = computed(() => state.value === 'valid')

  async function check(expr: string): Promise<void> {
    if (validateCronShape(expr)) {
      state.value = 'invalid'
      failureMessage.value = ''
      previewTimes.value = []
      return
    }
    requestSeq += 1
    const seq = requestSeq
    controller?.abort()
    controller = new AbortController()
    state.value = 'checking'
    try {
      const result = await validateCron({ cronExpr: expr, signal: controller.signal })
      if (seq !== requestSeq) return
      state.value = 'valid'
      failureMessage.value = ''
      previewTimes.value = result.next_run_times.map(formatBeijingTime)
    } catch (error) {
      if (isAbortError(error) || seq !== requestSeq) return
      state.value = 'invalid'
      previewTimes.value = []
      failureMessage.value = presentJobError(error, 'cron 预览失败，请稍后重试。')
    }
  }

  watch(
    cronExpr,
    (next) => {
      if (timer !== null) clearTimeout(timer)
      const shapeError = validateCronShape(next)
      if (shapeError) {
        // 形状都不对就不发请求：避免每敲一个字符打一次接口。
        state.value = 'invalid'
        failureMessage.value = ''
        previewTimes.value = []
        return
      }
      state.value = 'checking'
      previewTimes.value = []
      timer = setTimeout(() => void check(next), debounceMs)
    },
    { immediate: true },
  )

  onScopeDispose(() => {
    if (timer !== null) clearTimeout(timer)
    controller?.abort()
  })

  return { state, previewTimes, shapeMessage, message, canSubmit }
}
