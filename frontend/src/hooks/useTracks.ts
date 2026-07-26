import { useInfiniteQuery } from '@tanstack/react-query'
import { listTracks } from '@/lib/api'
import type { SortKey } from '@/lib/types'

export function useTracks(q: string, sort: SortKey) {
  return useInfiniteQuery({
    queryKey: ['tracks', q, sort],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listTracks({ q: q || undefined, sort, cursor: pageParam, limit: 100 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
}
