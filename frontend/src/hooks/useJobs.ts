import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelJob, getJob, listJobs, type ListJobsParams } from '@/lib/api'

export function useJobList(params: ListJobsParams = {}) {
  return useQuery({
    queryKey: ['jobs', params.state ?? 'all', params.cursor ?? null, params.limit ?? 100],
    queryFn: () => listJobs(params),
    refetchInterval: 2000, // cheap fallback poll — useJobEvents covers live detail views
  })
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: ['job', id],
    queryFn: () => getJob(id as number),
    enabled: id !== null,
  })
}

export function useCancelJob() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (jobId: number) => cancelJob(jobId),
    onSuccess: (_data, jobId) => {
      queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}
