// Mirrors muzilla.api.schemas.tracks / muzilla.api.schemas.auth field-for-field.

export interface TrackSummary {
  id: number
  path: string
  filename: string
  ext: string
  title: string | null
  artist: string | null
  album: string | null
  album_artist: string | null
  track_no: number | null
  disc_no: number | null
  year: number | null
  genre: string[]
  duration_ms: number | null
  format: string | null
  bitrate: number | null
  has_embedded_art: boolean
  probe_error: string | null
  missing_since: string | null
}

export interface TrackPage {
  items: TrackSummary[]
  next_cursor: string | null
  total: number
}

export interface AuthStatus {
  enabled: boolean
  authenticated: boolean
}

export type SortKey = 'title' | 'artist' | 'album' | 'added'
