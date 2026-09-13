import { computed, ref, watch } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { getTaskPolicy, getTaskPolicyChanges, updateTaskPolicy, type TaskPolicy } from '@/api/tasks'
import { presentJobError } from '../model/job-error'

export function useTaskPolicy() {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['task-policy'],
    queryFn: ({ signal }) => getTaskPolicy(signal),
    refetchOnWindowFocus: false,
  })
  const changes = useQuery({
    queryKey: ['task-policy', 'changes'],
    queryFn: ({ signal }) => getTaskPolicyChanges(signal),
  })
  const draft = ref<TaskPolicy | null>(null)
  const saving = ref(false)
  const error = ref('')
  const feedback = ref('')
  watch(
    query.data,
    (value) => {
      if (value && !draft.value) draft.value = { ...value }
    },
    { immediate: true },
  )

  async function save(): Promise<void> {
    if (!draft.value || saving.value) return
    const values = [
      draft.value.max_retries,
      draft.value.retry_delay_seconds,
      draft.value.history_retention_days,
    ]
    const ranges = [
      [0, 20],
      [1, 86400],
      [1, 36500],
    ] as const
    if (
      values.some(
        (value, index) =>
          !Number.isInteger(value) || value < ranges[index]![0] || value > ranges[index]![1],
      )
    ) {
      error.value = '请输入范围内的整数：重试 0–20 次，首次间隔 1–86400 秒，历史 1–36500 天。'
      return
    }
    saving.value = true
    error.value = ''
    feedback.value = ''
    try {
      const result = await updateTaskPolicy({ ...draft.value })
      draft.value = { ...result }
      client.setQueryData(['task-policy'], result)
      void changes.refetch()
      feedback.value = '默认策略已保存，仅影响之后受理的执行。'
    } catch (cause) {
      error.value = presentJobError(cause, '保存结果尚未确认，请重新读取默认策略与修改记录后核对。')
    } finally {
      saving.value = false
    }
  }

  async function reload(): Promise<void> {
    if (saving.value) return
    const result = await query.refetch()
    if (result.data && !result.isError) draft.value = { ...result.data }
    void changes.refetch()
  }

  return {
    draft,
    saving,
    error,
    feedback,
    query,
    changes,
    save,
    reload,
    loadError: computed(() =>
      query.isError.value ? presentJobError(query.error.value, '读取默认策略失败，请重试。') : '',
    ),
  }
}
