import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { analyzeTrack, getTrack, listTracks, rescanTrack } from '@/lib/api'
import type { FacetState } from '@/hooks/useTrackFacets'
import type { SortKey } from '@/lib/types'

export function useTracks(q: string, sort: SortKey, direction: 'asc' | 'desc', facets: FacetState) {
  const flags = [...facets.flags]
  return useInfiniteQuery({
    queryKey: ['tracks', q, sort, direction, facets.artist, facets.album, facets.genre, facets.format, flags],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listTracks({
        q: q || undefined,
        sort,
        direction,
        cursor: pageParam,
        limit: 100,
        artist: facets.artist ?? undefined,
        album: facets.album ?? undefined,
        genre: facets.genre ?? undefined,
        format: facets.format ?? undefined,
        flags: flags.length > 0 ? flags : undefined,
      }),
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

export function useTrack(id: number | null) {
  return useQuery({
    queryKey: ['track', id],
    queryFn: () => getTrack(id as number),
    enabled: id !== null,
  })
}

export function useRescanTrack() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: rescanTrack,
    onSuccess: (_job, trackId) => {
      queryClient.invalidateQueries({ queryKey: ['track', trackId] })
      queryClient.invalidateQueries({ queryKey: ['tracks'] })
    },
  })
}

export function useAnalyzeTrack() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: analyzeTrack,
    onSuccess: (_job, trackId) => {
      queryClient.invalidateQueries({ queryKey: ['track', trackId] })
      queryClient.invalidateQueries({ queryKey: ['tracks'] })
    },
  })
}
