import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { detectDuplicates, dismissDuplicate, listDuplicates } from '@/lib/api'

export function useDuplicateGroups(includeDismissed = false) {
  return useQuery({
    queryKey: ['duplicates', includeDismissed],
    queryFn: () => listDuplicates(includeDismissed),
  })
}

export function useDismissDuplicate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (groupId: number) => dismissDuplicate(groupId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['duplicates'] }),
  })
}

export function useDetectDuplicates() {
  return useMutation({
    mutationFn: detectDuplicates,
  })
}
