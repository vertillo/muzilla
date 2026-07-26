import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applyChangeset,
  bulkEditTracks,
  getChangeset,
  listChangesets,
  patchChangeDecisions,
  patchTrack,
  stripTracks,
  undoChangeset,
  type BulkEditField,
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

export function useApplyChangeset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changeSetId: number) => applyChangeset(changeSetId),
    onSuccess: (_data, changeSetId) => {
      queryClient.invalidateQueries({ queryKey: ['changeset', changeSetId] })
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
      queryClient.invalidateQueries({ queryKey: ['tracks'] })
    },
  })
}

export function useUndoChangeset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changeSetId: number) => undoChangeset(changeSetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
    },
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

export function useBulkEditTracks() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ trackIds, fields }: { trackIds: number[]; fields: BulkEditField[] }) =>
      bulkEditTracks(trackIds, fields),
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
