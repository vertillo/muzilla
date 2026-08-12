import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getProviderStatus, testProviderConnection } from '@/lib/api'

/** Shared by the Dashboard provider-health panel and any future status
 * surface. A single query key avoids duplicate requests and disconnected
 * provider-state projections. */
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
