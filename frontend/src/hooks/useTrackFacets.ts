import { useQuery } from '@tanstack/react-query'
import { getTrackFacets } from '@/lib/api'
import type { TrackSummary } from '@/lib/types'

export type FacetKey = 'missing-art' | 'unmatched' | 'errored'

export interface FacetState {
  artist: string | null
  album: string | null
  genre: string | null
  format: string | null
  flags: Set<FacetKey>
}

export const EMPTY_FACETS: FacetState = {
  artist: null,
  album: null,
  genre: null,
  format: null,
  flags: new Set(),
}

export interface FacetOptions {
  artists: string[]
  albums: string[]
  genres: string[]
  formats: string[]
}

const EMPTY_OPTIONS: FacetOptions = { artists: [], albums: [], genres: [], formats: [] }

/** Filter dropdown options, computed server-side over the *entire* table
 * by the product contract rather than derived from
 * whatever pages the catalog's infinite query happens to have loaded —
 * the client-derived version missed every option past the loaded pages
 * on a large library.
 *
 * Scoped to the current search string only, not to the other active
 * facets (artist/album/genre/format/flags). This is a deliberate choice
 * between the two standard faceted-search UX patterns:
 *
 *   - "narrow as you go": each facet's options reflect every *other*
 *     active facet, so picking an artist immediately shrinks the album
 *     dropdown to only that artist's albums.
 *   - "search narrows, facets don't narrow each other": all four
 *     dropdowns always show every value matching the current search text,
 *     regardless of which facets are already selected.
 *
 * This picks the second. Reasoning: "narrow as you go" requires a
 * separate facet query per dropdown (each excluding its own filter from
 * the query, or the artist dropdown would collapse to one option the
 * moment you picked an artist) — four times the query cost for a benefit
 * that's actually a UX hazard here: a track table with independent
 * artist/album/genre/format filters is not a strict hierarchy (an album
 * can appear under multiple genres in a mixed-tag library), so options
 * disappearing out from under a half-built filter combination is more
 * often confusing than helpful. Keying only on `q` is simpler, cheaper
 * (one query, reused across all four dropdowns), and predictable: the
 * options only change when the user changes what they're searching for. */
export function useFacetOptions(search: string): FacetOptions {
  const { data } = useQuery({
    queryKey: ['track-facets', search],
    queryFn: () => getTrackFacets(search || undefined),
    staleTime: 30_000,
  })
  if (!data) return EMPTY_OPTIONS
  return {
    artists: data.artists.map((f) => f.value),
    albums: data.albums.map((f) => f.value),
    genres: data.genres.map((f) => f.value),
    formats: data.formats.map((f) => f.value),
  }
}

/** Client-side filtering of already-loaded rows only — kept for
 * immediate responsiveness while the matching server-side page is still
 * being fetched (the infinite query already sends artist/album/genre/
 * format/flags to the server per docs/product-spec.md's cursor-pagination
 * contract, so this is a redundant-but-harmless re-filter of rows that
 * already match, not the source of truth for what's included). The
 * server-side query, not this function, is what the "N of M tracks"
 * counter and pagination now rely on. */
export function applyFacets(tracks: TrackSummary[], facets: FacetState): TrackSummary[] {
  return tracks.filter((t) => {
    if (facets.artist && t.artist !== facets.artist) return false
    if (facets.album && t.album !== facets.album) return false
    if (facets.genre && !t.genre.includes(facets.genre)) return false
    if (facets.format && t.format !== facets.format) return false
    if (facets.flags.has('missing-art') && t.has_embedded_art) return false
    if (facets.flags.has('unmatched') && t.album) return false
    if (facets.flags.has('errored') && !t.probe_error) return false
    return true
  })
}
