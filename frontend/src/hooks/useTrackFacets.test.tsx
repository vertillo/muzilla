import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetState } from '@/hooks/useTrackFacets'
import type { TrackFacets, TrackSummary } from '@/lib/types'

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

function fakeFacets(): TrackFacets {
  return {
    artists: [
      { value: 'Alpha', count: 2 },
      { value: 'Bravo', count: 1 },
    ],
    albums: [
      { value: 'X', count: 2 },
      { value: 'Y', count: 1 },
    ],
    genres: [
      { value: 'Indie', count: 1 },
      { value: 'Rock', count: 1 },
    ],
    formats: [
      { value: 'flac', count: 1 },
      { value: 'mp3', count: 2 },
    ],
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useFacetOptions', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => fakeFacets(),
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches facet options from the server, not from loaded rows', async () => {
    const { result } = renderHook(() => useFacetOptions(''), { wrapper })

    await waitFor(() =>
      expect(result.current).toEqual({
        artists: ['Alpha', 'Bravo'],
        albums: ['X', 'Y'],
        genres: ['Indie', 'Rock'],
        formats: ['flac', 'mp3'],
      }),
    )
    expect(fetchMock).toHaveBeenCalledWith('/api/tracks/facets', expect.anything())
  })

  it('scopes the facets request to the current search string', async () => {
    renderHook(() => useFacetOptions('Kveikur'), { wrapper })

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/tracks/facets?q=Kveikur', expect.anything()),
    )
  })

  it('returns empty options before the query resolves', () => {
    const { result } = renderHook(() => useFacetOptions(''), { wrapper })
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
