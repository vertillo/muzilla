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
    onSuccess: invalidate,
  })
}

export function useSplitGroup() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: ({ groupId, trackIds }: { groupId: number; trackIds: number[] }) =>
      splitGroup(groupId, trackIds),
    onSuccess: invalidate,
  })
}

export function useReassignTrack() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: ({ trackId, toGroupId }: { trackId: number; toGroupId: number }) =>
      reassignTrack(trackId, toGroupId),
    onSuccess: invalidate,
  })
}

export function useForceToSingleton() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: (trackId: number) => forceToSingleton(trackId),
    onSuccess: invalidate,
  })
}

export function usePinGroup() {
  const invalidate = useInvalidateGroups()
  return useMutation({
    mutationFn: (groupId: number) => pinGroup(groupId),
    onSuccess: invalidate,
  })
}
