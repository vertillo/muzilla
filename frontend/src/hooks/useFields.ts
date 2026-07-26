import { useQuery } from '@tanstack/react-query'
import { listFields } from '@/lib/api'

export function useFields() {
  return useQuery({
    queryKey: ['fields'],
    queryFn: listFields,
    staleTime: Infinity, // the field registry is static for the process lifetime
  })
}
