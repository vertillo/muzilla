import { describe, expect, it } from 'vitest'
import { commonValue, MULTIPLE_VALUES } from '@/lib/tagEditor'
import type { TrackDetail } from '@/lib/types'

function track(overrides: Partial<TrackDetail>): TrackDetail {
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
    artists: ['Artist'],
    composer: null,
    track_total: 10,
    disc_total: 1,
    original_year: 2020,
    date: '2020-01-01',
    compilation: false,
    label: null,
    catalog_number: null,
    barcode: null,
    isrc: null,
    country: null,
    media: null,
    mood: [],
    bpm: null,
    key: null,
    mb_track_id: null,
    mb_release_id: null,
    mb_recording_id: null,
    mb_artist_id: null,
    discogs_release_id: null,
    deezer_track_id: null,
    acoustid_id: null,
    sample_rate: null,
    ...overrides,
  } as TrackDetail
}

describe('commonValue', () => {
  it('returns null for an empty track list', () => {
    expect(commonValue([], 'title')).toBeNull()
  })

  it('returns the shared string value when every track agrees', () => {
    const tracks = [track({ artist: 'Sigur Rós' }), track({ artist: 'Sigur Rós' })]
    expect(commonValue(tracks, 'artist')).toBe('Sigur Rós')
  })

  it('returns the shared numeric value when every track agrees', () => {
    const tracks = [track({ year: 2020 }), track({ year: 2020 })]
    expect(commonValue(tracks, 'year')).toBe(2020)
  })

  it('returns the shared boolean value when every track agrees', () => {
    const tracks = [track({ compilation: true }), track({ compilation: true })]
    expect(commonValue(tracks, 'compilation')).toBe(true)
  })

  it('returns the shared array value (deep-equal, not reference-equal) when every track agrees', () => {
    const tracks = [track({ genre: ['Rock', 'Indie'] }), track({ genre: ['Rock', 'Indie'] })]
    expect(commonValue(tracks, 'genre')).toEqual(['Rock', 'Indie'])
  })

  it('returns null when every track shares a null value', () => {
    const tracks = [track({ label: null }), track({ label: null })]
    expect(commonValue(tracks, 'label')).toBeNull()
  })

  it('returns the MULTIPLE_VALUES sentinel — not the first value — when tracks disagree', () => {
    const tracks = [track({ artist: 'A' }), track({ artist: 'B' })]
    expect(commonValue(tracks, 'artist')).toBe(MULTIPLE_VALUES)
  })

  it('is the classic bulk-edit trap guard: one track differing among many still yields MULTIPLE_VALUES', () => {
    const tracks = [track({ year: 2020 }), track({ year: 2020 }), track({ year: 1999 })]
    expect(commonValue(tracks, 'year')).toBe(MULTIPLE_VALUES)
  })

  it('treats a null vs a real value as disagreement, not as "some data"', () => {
    const tracks = [track({ label: 'Warp' }), track({ label: null })]
    expect(commonValue(tracks, 'label')).toBe(MULTIPLE_VALUES)
  })

  it('treats array order as significant when comparing for agreement', () => {
    const tracks = [track({ genre: ['Rock', 'Indie'] }), track({ genre: ['Indie', 'Rock'] })]
    expect(commonValue(tracks, 'genre')).toBe(MULTIPLE_VALUES)
  })

  it('returns null for a single track with no disagreement possible', () => {
    expect(commonValue([track({ artist: 'Solo' })], 'artist')).toBe('Solo')
  })
})
