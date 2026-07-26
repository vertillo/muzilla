// Mirrors muzilla.api.schemas.tracks / muzilla.api.schemas.auth /
// muzilla.api.schemas.changesets / muzilla.api.schemas.groups /
// muzilla.api.schemas.fields field-for-field.

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

export interface TrackDetail extends TrackSummary {
  artists: string[]
  composer: string | null
  track_total: number | null
  disc_total: number | null
  original_year: number | null
  date: string | null
  compilation: boolean
  label: string | null
  catalog_number: string | null
  barcode: string | null
  isrc: string | null
  country: string | null
  media: string | null
  mood: string[]
  bpm: number | null
  key: string | null
  mb_track_id: string | null
  mb_release_id: string | null
  mb_recording_id: string | null
  mb_artist_id: string | null
  discogs_release_id: string | null
  deezer_track_id: string | null
  acoustid_id: string | null
  sample_rate: number | null
  channels: number | null
  codec: string | null
  comment: string | null
  encoder: string | null
  extra_tags: Record<string, string>
  group_id: number | null
  first_seen_at: string
  last_scanned_at: string
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

// --- changes/differ.py FieldDiff -----------------------------------------

export type InlineSpanOp = 'equal' | 'insert' | 'delete'

export interface InlineSpan {
  op: InlineSpanOp
  text: string
}

export interface MultiValueDiff {
  added: string[]
  removed: string[]
  unchanged: string[]
}

export interface BinaryDiff {
  old_summary: string | null
  new_summary: string | null
  old_blob_id: number | null
  new_blob_id: number | null
}

export type DiffKind = 'text' | 'multi_text' | 'binary' | 'scalar'

export interface FieldDiff {
  field: string
  label: string
  kind: DiffKind
  old_value: unknown
  new_value: unknown
  severity: 'normal' | 'destructive'
  old_spans: InlineSpan[]
  new_spans: InlineSpan[]
  multi: MultiValueDiff | null
  binary: BinaryDiff | null
}

// --- change_sets / changes -------------------------------------------------

export type ChangeDecisionValue = 'pending' | 'accepted' | 'rejected'
export type ChangeApplyState = 'pending' | 'applied' | 'failed' | 'conflicted'
export type ChangeSetState =
  | 'draft'
  | 'applying'
  | 'applied'
  | 'partially_applied'
  | 'failed'
  | 'discarded'
  | 'reverted'

export interface Change {
  id: number
  seq: number
  entity_type: string
  entity_id: number
  field: string
  op: string
  old_value: unknown
  new_value: unknown
  confidence: number | null
  severity: 'normal' | 'destructive'
  decision: ChangeDecisionValue
  apply_state: ChangeApplyState
  is_manual: boolean
  diff: FieldDiff
}

export interface ChangeSetSummary {
  id: number
  title: string
  source: string
  state: ChangeSetState
  scope_type: string
  scope_id: number | null
  created_by: string
  candidate_source: string | null
  candidate_ref: string | null
  undo_of_id: number | null
  stats: Record<string, number>
  error: string | null
}

export interface ChangeSetDetail extends ChangeSetSummary {
  changes: Change[]
}

export interface ChangeSetPage {
  items: ChangeSetSummary[]
  next_cursor: string | null
  total: number
}

export interface ChangeDecisionInput {
  change_id: number
  decision: ChangeDecisionValue
  new_value?: unknown
}

export interface ApplyResult {
  change_set_id: number
  state: string
  applied_track_ids: number[]
  conflicted_track_ids: number[]
  errors: Record<number, string>
}

// --- track_groups -----------------------------------------------------------

export interface GroupSummary {
  id: number
  key: string
  kind: string
  grouping_basis: string | null
  grouping_confidence: number | null
  is_pinned: boolean
  album: string | null
  album_artist: string | null
  year: number | null
  track_count: number
  expected_track_count: number | null
  match_state: string
}

export interface GroupDetail extends GroupSummary {
  track_ids: number[]
}

export interface RunCascadeResult {
  groups_created: number
  groups_updated: number
  tracks_grouped: number
  tracks_skipped_pinned: number
}

// --- domain/fields.py registry ----------------------------------------------

export interface FieldInfo {
  name: string
  label: string
  type: 'text' | 'int' | 'float' | 'date' | 'bool' | 'multi_text'
  category: string
  editable: boolean
  multi_valued: boolean
  default_strip: boolean
}
