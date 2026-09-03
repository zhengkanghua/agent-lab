export const scheduledJobKeys = {
  all: ['scheduled-jobs'] as const,
  jobs: () => [...scheduledJobKeys.all, 'jobs'] as const,
  runs: (jobId: string) => [...scheduledJobKeys.all, 'runs', jobId] as const,
}
