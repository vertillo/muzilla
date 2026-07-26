import { useInfiniteQuery, useQueries } from '@tanstack/react-query'
import { getTrack, listTracks } from '@/lib/api'
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

/** Fetches full TrackDetail for each id in parallel — used by the
 * manual tag editor (single + bulk), which needs the current value of
 * every editable field, not just the catalog table's summary shape. */
export function useTrackDetails(ids: number[]) {
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['track', id],
      queryFn: () => getTrack(id),
    })),
  })
  return {
    tracks: results.map((r) => r.data).filter((t) => t !== undefined),
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
  }
}
