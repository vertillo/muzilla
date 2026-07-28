import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetState } from '@/hooks/useTrackFacets'
import type { TrackSummary } from '@/lib/types'

function track(overrides: Partial<TrackSummary>): TrackSummary {
  return {
    id: 1,
    path: '/music/a.mp3',
    filename: 'a.mp3',
    ext: 'mp3',
    title: 'Title',
    artist: 'Artist',
    album: 'Album',
    album_artist: 'Artist',
    track_no: 1,
    disc_no: 1,
    year: 2020,
    genre: ['Rock'],
    duration_ms: 180_000,
    format: 'mp3',
    bitrate: 320,
    has_embedded_art: true,
    has_lyrics: false,
    probe_error: null,
    missing_since: null,
    ...overrides,
  }
}

describe('useFacetOptions', () => {
  it('collects unique, sorted values across artist/album/genre/format', () => {
    const tracks = [
      track({ id: 1, artist: 'Bravo', album: 'Y', genre: ['Rock', 'Indie'], format: 'flac' }),
      track({ id: 2, artist: 'Alpha', album: 'X', genre: ['Indie'], format: 'mp3' }),
      track({ id: 3, artist: 'Alpha', album: 'X', genre: [], format: 'mp3' }),
    ]
    const { result } = renderHook(() => useFacetOptions(tracks))
    expect(result.current).toEqual({
      artists: ['Alpha', 'Bravo'],
      albums: ['X', 'Y'],
      genres: ['Indie', 'Rock'],
      formats: ['flac', 'mp3'],
    })
  })

  it('omits null artist/album/format and empty genre lists', () => {
    const tracks = [track({ artist: null, album: null, genre: [], format: null })]
    const { result } = renderHook(() => useFacetOptions(tracks))
    expect(result.current).toEqual({ artists: [], albums: [], genres: [], formats: [] })
  })

  it('returns empty options for an empty track list', () => {
    const { result } = renderHook(() => useFacetOptions([]))
    expect(result.current).toEqual({ artists: [], albums: [], genres: [], formats: [] })
  })
})

describe('applyFacets', () => {
  const tracks = [
    track({ id: 1, artist: 'Alpha', album: 'X', genre: ['Rock'], format: 'mp3', has_embedded_art: true, probe_error: null }),
    track({ id: 2, artist: 'Bravo', album: 'Y', genre: ['Indie'], format: 'flac', has_embedded_art: false, probe_error: null }),
    track({ id: 3, artist: 'Alpha', album: null, genre: [], format: 'mp3', has_embedded_art: true, probe_error: 'bad header' }),
  ]

  it('returns every track when no facets are set', () => {
    expect(applyFacets(tracks, EMPTY_FACETS)).toEqual(tracks)
  })

  it('filters by artist', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, artist: 'Alpha' })
    expect(result.map((t) => t.id)).toEqual([1, 3])
  })

  it('filters by album', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, album: 'X' })
    expect(result.map((t) => t.id)).toEqual([1])
  })

  it('filters by genre (membership, not exact match)', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, genre: 'Indie' })
    expect(result.map((t) => t.id)).toEqual([2])
  })

  it('filters by format', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, format: 'flac' })
    expect(result.map((t) => t.id)).toEqual([2])
  })

  it('filters by the missing-art flag (tracks WITHOUT embedded art)', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, flags: new Set(['missing-art']) })
    expect(result.map((t) => t.id)).toEqual([2])
  })

  it('filters by the unmatched flag (tracks with no album tag)', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, flags: new Set(['unmatched']) })
    expect(result.map((t) => t.id)).toEqual([3])
  })

  it('filters by the errored flag (tracks with a probe error)', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, flags: new Set(['errored']) })
    expect(result.map((t) => t.id)).toEqual([3])
  })

  it('combines multiple flags as AND', () => {
    const result = applyFacets(tracks, {
      ...EMPTY_FACETS,
      flags: new Set(['unmatched', 'errored']),
    })
    expect(result.map((t) => t.id)).toEqual([3])
  })

  it('combines a scalar facet and a flag as AND', () => {
    const result = applyFacets(tracks, {
      ...EMPTY_FACETS,
      artist: 'Alpha',
      flags: new Set(['errored']),
    })
    expect(result.map((t) => t.id)).toEqual([3])
  })

  it('returns nothing when facets exclude every track', () => {
    const result = applyFacets(tracks, { ...EMPTY_FACETS, artist: 'Nonexistent' })
    expect(result).toEqual([])
  })

  it('EMPTY_FACETS is the identity value applyFacets expects as a default', () => {
    const state: FacetState = EMPTY_FACETS
    expect(state.flags.size).toBe(0)
  })
})
