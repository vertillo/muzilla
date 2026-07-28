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
  has_lyrics: boolean
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
  lyrics_synced: boolean
  rg_track_gain: number | null
  rg_album_gain: number | null
  art_blob_id: number | null
}

export interface TrackPage {
  items: TrackSummary[]
  next_cursor: string | null
  total: number
}

export interface FacetValue {
  value: string
  count: number
}

export interface TrackFacets {
  artists: FacetValue[]
  albums: FacetValue[]
  genres: FacetValue[]
  formats: FacetValue[]
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

export interface ChangeSetEntity {
  entity_type: string
  entity_id: number
  label: string
  sort_key: number | null
}

export interface ChangeSetDetail extends ChangeSetSummary {
  changes: Change[]
  entities: ChangeSetEntity[]
}

export interface ChangeSetPage {
  items: ChangeSetSummary[]
  next_cursor: string | null
  total: number
}

// --- matching (api.schemas.matching) --------------------------------------
// docs/PLAN.md §9: candidate selection is release-level, never field-level
// — a row is one (source, release), and picking it re-stages the whole
// changeset. No per-field source dropdown, no field_sources config.

export interface CandidateRow {
  source: string
  ref_id: string
  album: string | null
  album_artist: string | null
  year: number | null
  label: string | null
  catalog_number: string | null
  track_count: number
  distance: number
  adjusted_distance: number
  is_duplicate_of: number[]
  corroborated_by: string[]
}

export interface MatchProposal {
  candidates: CandidateRow[]
  auto_applicable: boolean
  needs_confirmation: boolean
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

// --- jobs (api.schemas.jobs) -------------------------------------------------
// docs/PLAN.md §9: SSE, not WebSockets — GET /api/jobs/{id}/events
// replays from job_events (?after=<seq>) then streams new ones.

export type JobState = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface JobSummary {
  id: number
  type: string
  state: JobState
  priority: number
  progress_current: number
  progress_total: number | null
  progress_message: string | null
  attempts: number
  error: string | null
}

export interface JobDetail extends JobSummary {
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
}

export interface JobPage {
  items: JobSummary[]
  next_cursor: string | null
}

export interface JobEnqueued {
  job_id: number
}

export type JobEventKind = 'progress' | 'log' | 'state'

export interface JobEvent {
  seq: number
  kind: JobEventKind
  payload: Record<string, unknown>
}

// --- import sessions (api.schemas.imports) -----------------------------------

export type ImportSessionState =
  | 'pending'
  | 'scanning'
  | 'fingerprinting'
  | 'grouping'
  | 'matching'
  | 'reviewing'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type ImportTaskState = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface ImportTask {
  stage: string
  seq: number
  state: ImportTaskState
  error: string | null
}

export interface ImportSessionSummary {
  id: number
  library_root: string
  state: ImportSessionState
  job_id: number | null
  stats: Record<string, unknown>
  error: string | null
}

export interface ImportSessionDetail extends ImportSessionSummary {
  tasks: ImportTask[]
  changeset_ids: number[]
}

// --- paths (api.schemas.paths) --------------------------------------------

export interface PathPreviewRow {
  track_id: number
  old_path: string
  new_path: string
  errors: string[]
  is_collision: boolean
}

// --- duplicates (api.schemas.duplicates) ------------------------------------
// docs/PLAN.md §Phase-6: fingerprint-based duplicate detection. Detection
// only — there is no delete action; see db/models.py's DuplicateGroup
// docstring for why.

export interface DuplicateTrack {
  id: number
  path: string
  title: string | null
  artist: string | null
  format: string | null
  bitrate: number | null
  duration_ms: number | null
}

export interface DuplicateGroup {
  id: number
  mb_recording_id: string
  basis: string
  dismissed: boolean
  tracks: DuplicateTrack[]
}

export interface DuplicateGroupList {
  items: DuplicateGroup[]
}

// --- dashboard (api.schemas.dashboard) --------------------------------------

export interface DashboardSummary {
  total_tracks: number
  tracks_missing: number
  tracks_with_errors: number
  tracks_missing_art: number
  album_count: number
  singleton_count: number
  ungrouped_track_count: number
}

// --- providers (api.schemas.providers) --------------------------------------

export interface ProviderStatus {
  provider: string
  enabled: boolean
  requires_auth: boolean
  token_configured: boolean
  live: boolean
  last_success_at: string | null
  last_error_at: string | null
  last_error_detail: string | null
  rate_limited: boolean
}

export interface ProviderStatusList {
  items: ProviderStatus[]
}
