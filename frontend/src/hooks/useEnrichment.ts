import { useMutation, useQueryClient } from '@tanstack/react-query'
import { enrichArt, enrichLyrics, enrichReplaygain } from '@/lib/api'

function useInvalidateJobs() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['jobs'] })
}

export function useEnrichReplaygain() {
  const invalidate = useInvalidateJobs()
  return useMutation({ mutationFn: enrichReplaygain, onSuccess: invalidate })
}

export function useEnrichArt() {
  const invalidate = useInvalidateJobs()
  return useMutation({ mutationFn: enrichArt, onSuccess: invalidate })
}

export function useEnrichLyrics() {
  const invalidate = useInvalidateJobs()
  return useMutation({ mutationFn: enrichLyrics, onSuccess: invalidate })
}
