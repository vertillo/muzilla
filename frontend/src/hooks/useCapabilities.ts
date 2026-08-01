import { useQuery } from '@tanstack/react-query'
import { getRuntimeCapabilities } from '@/lib/api'

export function useCapabilities() {
  return useQuery({
    queryKey: ['runtime-capabilities'],
    queryFn: getRuntimeCapabilities,
    staleTime: 15_000,
    refetchInterval: 30_000,
  })
}
