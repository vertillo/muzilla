import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getGroupCandidates,
  getTrackCandidates,
  stageGroupMatch,
  stageTrackMatch,
} from '@/lib/api'

/** GET-only: fetches and ranks candidates but stages nothing.
 * scope determines which endpoint to call — a changeset's scope_type
 * is always 'group' or 'track' (docs/PLAN.md §9's two review modes). */
export function useCandidates(scopeType: string, scopeId: number | null) {
  return useQuery({
    queryKey: ['candidates', scopeType, scopeId],
    queryFn: () =>
      scopeType === 'group' ? getGroupCandidates(scopeId as number) : getTrackCandidates(scopeId as number),
    enabled: scopeId !== null && (scopeType === 'group' || scopeType === 'track'),
  })
}

/** Re-stages the whole changeset from one chosen (source, ref_id) —
 * always creates a fresh match_proposal changeset from scratch, never
 * merges with whatever changeset the user was already looking at
 * (docs/PLAN.md §3: one release, one source, no per-field merging). */
export function useStageMatch(scopeType: string, scopeId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ source, refId }: { source: string; refId: string }) => {
      if (scopeId === null) throw new Error('no scope id')
      return scopeType === 'group'
        ? stageGroupMatch(scopeId, source, refId)
        : stageTrackMatch(scopeId, source, refId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
      queryClient.invalidateQueries({ queryKey: ['candidates', scopeType, scopeId] })
    },
  })
}
