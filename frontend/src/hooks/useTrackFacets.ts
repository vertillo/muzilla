import { useMemo } from 'react'
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

export function useFacetOptions(tracks: TrackSummary[]): FacetOptions {
  return useMemo(() => {
    const artists = new Set<string>()
    const albums = new Set<string>()
    const genres = new Set<string>()
    const formats = new Set<string>()
    for (const t of tracks) {
      if (t.artist) artists.add(t.artist)
      if (t.album) albums.add(t.album)
      for (const g of t.genre) genres.add(g)
      if (t.format) formats.add(t.format)
    }
    return {
      artists: [...artists].sort(),
      albums: [...albums].sort(),
      genres: [...genres].sort(),
      formats: [...formats].sort(),
    }
  }, [tracks])
}

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
