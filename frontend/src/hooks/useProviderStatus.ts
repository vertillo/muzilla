import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getProviderStatus, testProviderConnection } from '@/lib/api'

/** Shared by the Dashboard's provider health panel and (were a second
 * indicator ever needed) any other screen — a single query key means
 * both consumers share one cached fetch rather than issuing duplicate
 * requests, per docs/PHASE8_BRIEF.md Phase 7 suggestion #7's note to
 * "reuse it in the Dashboard rather than building a second disconnected
 * indicator" once one exists. */
export function useProviderStatus() {
  return useQuery({
    queryKey: ['provider-status'],
    queryFn: getProviderStatus,
    staleTime: 15_000,
  })
}

export function useTestProviderConnection() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: testProviderConnection,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['provider-status'] }),
  })
}
