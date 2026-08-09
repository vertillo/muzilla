import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applyChangeset,
  getChangeset,
  listChangesets,
  patchChangeDecisions,
  patchTrack,
  stripTracks,
  undoChangeset,
} from '@/lib/api'
import type { ChangeDecisionInput } from '@/lib/types'

export function useChangesetList(state?: string) {
  return useQuery({
    queryKey: ['changesets', state ?? 'all'],
    queryFn: () => listChangesets({ state, limit: 100 }),
  })
}

export function useChangeset(id: number | null) {
  return useQuery({
    queryKey: ['changeset', id],
    queryFn: () => getChangeset(id as number),
    enabled: id !== null,
  })
}

export function usePatchDecisions(changeSetId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (decisions: ChangeDecisionInput[]) => patchChangeDecisions(changeSetId, decisions),
    onSuccess: (data) => {
      queryClient.setQueryData(['changeset', changeSetId], data)
    },
  })
}

// apply/undo now enqueue a job and return { job_id } immediately
// (docs/PLAN.md §10: `POST .../apply -> 202 {job_id}`) — callers
// subscribe to the job via useJobEvents for progress/completion rather
// than getting an ApplyResult/ChangeSetDetail back synchronously.

export function useApplyChangeset() {
  return useMutation({
    mutationFn: (changeSetId: number) => applyChangeset(changeSetId),
  })
}

export function useUndoChangeset() {
  return useMutation({
    mutationFn: (changeSetId: number) => undoChangeset(changeSetId),
  })
}

export function usePatchTrack() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ trackId, fields }: { trackId: number; fields: Record<string, unknown> }) =>
      patchTrack(trackId, fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
    },
  })
}

export function useStripTracks() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (trackIds: number[]) => stripTracks(trackIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
    },
  })
}
