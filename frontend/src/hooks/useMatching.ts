import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getTrackCandidates, stageTrackMatch } from '@/lib/api'

/** Legacy candidate adapter for an individual track only. */
export function useCandidates(scopeType: string, scopeId: number | null) {
  return useQuery({
    queryKey: ['candidates', scopeType, scopeId],
    queryFn: () => getTrackCandidates(scopeId as number),
    enabled: scopeId !== null && scopeType === 'track',
  })
}

/** Re-stages the whole changeset from one chosen (source, ref_id) —
 * always creates a fresh match_proposal changeset from scratch, never
 * merges with whatever changeset the user was already looking at
 * (docs/product-spec.md: one release, one source, no per-field merging). */
export function useStageMatch(scopeType: string, scopeId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ source, refId }: { source: string; refId: string }) => {
      if (scopeId === null) throw new Error('no scope id')
      if (scopeType !== 'track') throw new Error('group matching is no longer public')
      return stageTrackMatch(scopeId, source, refId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
      queryClient.invalidateQueries({ queryKey: ['candidates', scopeType, scopeId] })
    },
  })
}
