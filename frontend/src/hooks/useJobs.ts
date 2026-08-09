import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelJob, getJob, listJobs, retryFailedLyrics, type ListJobsParams } from '@/lib/api'
import type { JobDetail, JobPage, JobSummary } from '@/lib/types'

export function useJobList(params: ListJobsParams = {}) {
  return useQuery({
    queryKey: ['jobs', params.state ?? 'all', params.cursor ?? null, params.limit ?? 100, params.includeSystem ?? false],
    queryFn: () => listJobs(params),
    refetchInterval: 2000, // cheap fallback poll — useJobEvents covers live detail views
  })
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: ['job', id],
    queryFn: () => getJob(id as number),
    enabled: id !== null,
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state === 'pending' || state === 'running' || state === 'cancelling' ? 500 : false
    },
  })
}

export function useCancelJob() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (jobId: number) => cancelJob(jobId),
    onSuccess: (detail, jobId) => {
      const { payload: _payload, result: _result, ...summary } = detail
      queryClient.setQueryData<JobDetail>(['job', jobId], detail)
      queryClient.setQueriesData<JobPage>({ queryKey: ['jobs'] }, (page) => {
        if (page === undefined) return page
        return {
          ...page,
          items: page.items.map((job): JobSummary => (job.id === jobId ? summary : job)),
        }
      })
      // Keep the immediate, persisted `cancelling` acknowledgement visible
      // before a fast handler reaches its next checkpoint and the periodic
      // list refresh fetches the terminal state.
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        queryClient.invalidateQueries({ queryKey: ['jobs'] })
      }, 100)
    },
  })
}

export function useRetryFailedLyrics() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (jobId: number) => retryFailedLyrics(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })
}
