import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  forceToSingleton,
  getGroup,
  listGroups,
  mergeGroups,
  pinGroup,
  reassignTrack,
  runGroupingCascade,
  splitGroup,
} from '@/lib/api'
import { pushToast } from '@/hooks/useToasts'
import type { ChangeSetDetail } from '@/lib/types'

/** Pin/merge/split/reassign/force-to-singleton now auto-apply
 * server-side (docs/KNOWN_BUGS.md #3's fix — Phase 7 item 6's product
 * decision) instead of staging a draft the user must separately review
 * and apply. A 200 response can still carry `state: "failed"` or
 * "partially_applied" (apply_changeset's own failure modes, e.g. a
 * conflicting concurrent edit) — that's not an HTTP error, so the
 * App.tsx-level MutationCache.onError toast (which only fires on a
 * thrown/rejected mutation) never sees it. This is the one place each
 * of these five mutations needs its own onSuccess toast, distinct from
 * the generic error-toast path. */
function toastForAppliedChangeset(cs: ChangeSetDetail, verb: string) {
  if (cs.state === 'applied') {
    pushToast({ tone: 'success', title: `${verb} applied` })
  } else {
    pushToast({
      tone: 'error',
      title: `${verb} did not fully apply`,
      description: `Changeset #${cs.id} is in state "${cs.state}" — open Changes to see details.`,
    })
  }
}

export function useGroupList() {
  return useQuery({
    queryKey: ['groups'],
    queryFn: listGroups,
  })
}

export function useGroup(id: number | null) {
  return useQuery({
    queryKey: ['group', id],
    queryFn: () => getGroup(id as number),
    enabled: id !== null,
  })
}

function useInvalidateGroups() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ['groups'] })
    queryClient.invalidateQueries({ queryKey: ['group'] })
    queryClient.invalidateQueries({ queryKey: ['changesets'] })
  }
}

export function useRunCascade() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: runGroupingCascade,
    onSuccess: invalidate,
  })
}

export function useMergeGroups() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: ({ intoGroupId, fromGroupIds }: { intoGroupId: number; fromGroupIds: number[] }) =>
      mergeGroups(intoGroupId, fromGroupIds),
    onSuccess: (cs) => {
      invalidate()
      toastForAppliedChangeset(cs, 'Merge')
    },
  })
}

export function useSplitGroup() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: ({ groupId, trackIds }: { groupId: number; trackIds: number[] }) =>
      splitGroup(groupId, trackIds),
    onSuccess: (cs) => {
      invalidate()
      toastForAppliedChangeset(cs, 'Split')
    },
  })
}

export function useReassignTrack() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: ({ trackId, toGroupId }: { trackId: number; toGroupId: number }) =>
      reassignTrack(trackId, toGroupId),
    onSuccess: (cs) => {
      invalidate()
      toastForAppliedChangeset(cs, 'Reassign')
    },
  })
}

export function useForceToSingleton() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: (trackId: number) => forceToSingleton(trackId),
    onSuccess: (cs) => {
      invalidate()
      toastForAppliedChangeset(cs, 'Force to singleton')
    },
  })
}

export function usePinGroup() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: (groupId: number) => pinGroup(groupId),
    onSuccess: (cs) => {
      invalidate()
      toastForAppliedChangeset(cs, 'Pin')
    },
  })
}
