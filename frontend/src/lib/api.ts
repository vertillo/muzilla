import type {
  AuthStatus,
  ChangeDecisionInput,
  ChangeSetDetail,
  ChangeSetPage,
  DuplicateGroup,
  DuplicateGroupList,
  ImportSessionDetail,
  ImportSessionSummary,
  JobDetail,
  JobEnqueued,
  JobPage,
  DashboardSummary,
  MatchProposal,
  ProviderSetting,
  ProviderStatus,
  ProviderStatusList,
  RuntimeCapabilities,
  ReviewBundleDetail,
  ReviewNeighbors,
  ReviewBundlePage,
  ReviewOperationDecision,
  SettingsSummary,
  TemplatePreviewResult,
  TemplateSettings,
  TrackDetail,
  TrackFacets,
  TrackPage,
} from '@/lib/types'
import type { components } from '@/lib/api-types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let csrfToken: string | null = null

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const mutationHeaders: Record<string, string> = method === 'GET' || method === 'HEAD' || !csrfToken
    ? {}
    : { 'X-CSRF-Token': csrfToken }
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...mutationHeaders, ...init?.headers },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }
  if (typeof body === 'object' && body !== null && typeof body.csrf_token === 'string') {
    csrfToken = body.csrf_token
  }
  return body as T
}

/** Generates a v4-shaped random id for the Idempotency-Key header on
 * mutating endpoints — good enough uniqueness for a
 * client-generated retry key, no crypto requirement. */
function idempotencyKey(): string {
  return crypto.randomUUID()
}

export interface ListTracksParams {
  q?: string
  sort?: string
  direction?: 'asc' | 'desc'
  cursor?: string
  limit?: number
  artist?: string
  album?: string
  genre?: string
  format?: string
  flags?: string[]
}

export function listTracks(params: ListTracksParams): Promise<TrackPage> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.sort) search.set('sort', params.sort)
  if (params.direction) search.set('direction', params.direction)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  if (params.artist) search.set('artist', params.artist)
  if (params.album) search.set('album', params.album)
  if (params.genre) search.set('genre', params.genre)
  if (params.format) search.set('format', params.format)
  if (params.flags && params.flags.length > 0) search.set('flags', params.flags.join(','))
  const qs = search.toString()
  return request<TrackPage>(`/api/tracks${qs ? `?${qs}` : ''}`)
}

export function getTrack(id: number): Promise<TrackDetail> {
  return request<TrackDetail>(`/api/tracks/${id}`)
}

export function rescanTrack(id: number): Promise<JobEnqueued> {
  return request<JobEnqueued>(`/api/tracks/${id}/rescan`, { method: 'POST' })
}

export function createManualTrackReview(id: number, fields: Record<string, unknown>): Promise<ReviewBundleDetail> {
  return request<ReviewBundleDetail>(`/api/tracks/${id}/review/manual`, {
    method: 'POST',
    body: JSON.stringify({ fields }),
  })
}

export function createGroupingReview(id: number): Promise<ReviewBundleDetail> {
  return request<ReviewBundleDetail>(`/api/tracks/${id}/review/grouping`, { method: 'POST' })
}

export function getTrackFacets(q?: string): Promise<TrackFacets> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : ''
  return request<TrackFacets>(`/api/tracks/facets${qs}`)
}

export function login(password: string): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
}

export function logout(): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/logout', { method: 'POST' })
}

export function authStatus(): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/status')
}

// --- fields ------------------------------------------------------------

export function listFields(): Promise<components['schemas']['FieldListOut']> {
  return request('/api/fields')
}

// --- changesets ----------------------------------------------------------

export interface ListChangesetsParams {
  state?: string
  cursor?: string
  limit?: number
}

export function listChangesets(params: ListChangesetsParams = {}): Promise<ChangeSetPage> {
  const search = new URLSearchParams()
  if (params.state) search.set('state', params.state)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  const qs = search.toString()
  return request<ChangeSetPage>(`/api/changesets${qs ? `?${qs}` : ''}`)
}

export function getChangeset(id: number): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/changesets/${id}`)
}

export function patchChangeDecisions(
  changeSetId: number,
  decisions: ChangeDecisionInput[],
): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/changesets/${changeSetId}/changes`, {
    method: 'PATCH',
    body: JSON.stringify({ decisions }),
  })
}

export function applyChangeset(id: number): Promise<JobEnqueued> {
  return request<JobEnqueued>(`/api/changesets/${id}/apply`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}

export function undoChangeset(id: number): Promise<JobEnqueued> {
  return request<JobEnqueued>(`/api/changesets/${id}/undo`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}

export function patchTrack(trackId: number, fields: Record<string, unknown>): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/tracks/${trackId}`, {
    method: 'PATCH',
    body: JSON.stringify({ fields }),
  })
}

export type FindReplaceParams = components['schemas']['FindReplaceRequest']

export function previewFindReplace(
  params: FindReplaceParams,
): Promise<components['schemas']['FindReplacePreviewOut']> {
  return request('/api/tracks/find-replace/preview', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export function applyFindReplace(params: FindReplaceParams): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>('/api/tracks/find-replace', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export function stripTracks(trackIds: number[]): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>('/api/tracks/strip', {
    method: 'POST',
    body: JSON.stringify({ track_ids: trackIds }),
  })
}

// --- matching --------------------------------------------------------------

export function getTrackCandidates(trackId: number): Promise<MatchProposal> {
  return request<MatchProposal>(`/api/tracks/${trackId}/candidates`)
}

export function stageTrackMatch(
  trackId: number,
  source: string,
  refId: string,
): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/tracks/${trackId}/stage`, {
    method: 'POST',
    body: JSON.stringify({ source, ref_id: refId }),
  })
}

export function chooseTrackCandidateForReview(
  trackId: number,
  source: string,
  refId: string,
): Promise<ReviewBundleDetail> {
  return request<ReviewBundleDetail>(`/api/tracks/${trackId}/review/candidate`, {
    method: 'POST',
    body: JSON.stringify({ source, ref_id: refId }),
  })
}

// --- jobs --------------------------------------------------------------

export interface ListJobsParams {
  state?: string
  cursor?: string
  limit?: number
  includeSystem?: boolean
}

export function listJobs(params: ListJobsParams = {}): Promise<JobPage> {
  const search = new URLSearchParams()
  if (params.state) search.set('state', params.state)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  if (params.includeSystem) search.set('include_system', 'true')
  const qs = search.toString()
  return request<JobPage>(`/api/jobs${qs ? `?${qs}` : ''}`)
}

export function getJob(id: number): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${id}`)
}

export function cancelJob(id: number): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${id}/cancel`, { method: 'POST' })
}

export function retryFailedLyrics(id: number): Promise<JobEnqueued> {
  return request<JobEnqueued>(`/api/enrich/lyrics/${id}/retry-failed`, { method: 'POST' })
}

// --- imports -------------------------------------------------------------

export function startScan(root: string): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/scan', {
    method: 'POST',
    body: JSON.stringify({ root }),
  })
}

export type ImportConfig = components['schemas']['ImportConfigOut']

export function getImportConfig(): Promise<ImportConfig> {
  return request<ImportConfig>('/api/imports/config')
}

export function startImport(libraryRoot: string): Promise<ImportSessionSummary> {
  return request<ImportSessionSummary>('/api/imports', {
    method: 'POST',
    body: JSON.stringify({ library_root: libraryRoot }),
  })
}

export function getImportSession(id: number): Promise<ImportSessionDetail> {
  return request<ImportSessionDetail>(`/api/imports/${id}`)
}

export function listImportSessions(limit = 5): Promise<components['schemas']['ImportSessionPageOut']> {
  return request(`/api/imports?limit=${limit}`)
}

export function resumeImport(id: number): Promise<ImportSessionSummary> {
  return request<ImportSessionSummary>(`/api/imports/${id}/resume`, { method: 'POST' })
}

// --- paths -----------------------------------------------------------------

export type PathPreviewParams = components['schemas']['PathPreviewRequest']

export function previewPaths(params: PathPreviewParams): Promise<components['schemas']['PathPreviewOut']> {
  return request('/api/paths/preview', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export function renamePaths(params: PathPreviewParams): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>('/api/paths/rename', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

// --- enrichment (api.schemas.jobs — each a thin one-off job enqueue) -------

export function enrichReplaygain(): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/enrich/replaygain', { method: 'POST' })
}

export function enrichArt(): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/enrich/art', { method: 'POST' })
}

export function enrichLyrics(): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/enrich/lyrics', { method: 'POST' })
}

// --- duplicates (api.schemas.duplicates) ------------------------------------

export function listDuplicates(includeDismissed = false): Promise<DuplicateGroupList> {
  const qs = includeDismissed ? '?include_dismissed=true' : ''
  return request<DuplicateGroupList>(`/api/duplicates${qs}`)
}

export function dismissDuplicate(groupId: number): Promise<DuplicateGroup> {
  return request<DuplicateGroup>(`/api/duplicates/${groupId}/dismiss`, { method: 'POST' })
}

export function detectDuplicates(): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/duplicates/detect', { method: 'POST' })
}

// --- blobs -------------------------------------------------------------------

export function blobUrl(blobId: number, size?: 'thumb'): string {
  return size ? `/api/blobs/${blobId}?size=${size}` : `/api/blobs/${blobId}`
}

// --- dashboard (api.schemas.dashboard) --------------------------------------

export function getDashboardSummary(): Promise<DashboardSummary> {
  return request<DashboardSummary>('/api/dashboard/summary')
}

// --- providers (api.schemas.providers) --------------------------------------

export function getProviderStatus(): Promise<ProviderStatusList> {
  return request<ProviderStatusList>('/api/providers/status')
}

export function testProviderConnection(provider: string): Promise<ProviderStatus> {
  return request<ProviderStatus>(`/api/providers/${provider}/test`, { method: 'POST' })
}

// --- runtime capabilities ---------------------------------------------------

export function getRuntimeCapabilities(): Promise<RuntimeCapabilities> {
  return request<RuntimeCapabilities>('/api/capabilities')
}

// --- settings (api.schemas.settings) ----------------------------------------

export function getSettings(): Promise<SettingsSummary> {
  return request<SettingsSummary>('/api/settings')
}

export type UpdateProviderSettingParams = components['schemas']['UpdateProviderSettingRequest']

export function updateProviderSetting(
  provider: string,
  params: UpdateProviderSettingParams,
): Promise<ProviderSetting> {
  return request<ProviderSetting>(`/api/settings/providers/${provider}`, {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

export type UpdateTemplatesParams = components['schemas']['UpdateTemplatesRequest']

export function updateTemplates(params: UpdateTemplatesParams): Promise<TemplateSettings> {
  return request<TemplateSettings>('/api/settings/templates', {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

export function updateStripFields(fields: string[]): Promise<string[]> {
  return request<string[]>('/api/settings/strip-fields', {
    method: 'PUT',
    body: JSON.stringify({ fields }),
  })
}

export function previewTemplate(template: string): Promise<TemplatePreviewResult> {
  return request<TemplatePreviewResult>('/api/settings/templates/preview', {
    method: 'POST',
    body: JSON.stringify({ template }),
  })
}

export function resetCatalogAndActivity(
  params: components['schemas']['CatalogResetRequest'],
  key: string,
): Promise<components['schemas']['ResetResultOut']> {
  return request('/api/settings/reset/catalog', {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(params),
  })
}

export function factoryReset(
  params: components['schemas']['FactoryResetRequest'],
  key: string,
): Promise<components['schemas']['ResetResultOut']> {
  return request('/api/settings/reset/factory', {
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    body: JSON.stringify(params),
  })
}

// ReviewBundle has no replacement UI in this slice.  Keeping this generated contract
// client-side makes the temporary read route compile-checked until that UI lands.
export function getReviewBundle(id: number): Promise<ReviewBundleDetail> {
  return request<ReviewBundleDetail>(`/api/reviews/${id}`)
}

export function getReviewNeighbors(id: number, params: ListReviewsParams = {}): Promise<ReviewNeighbors> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.state?.length) search.set('state', params.state.join(','))
  if (params.confidence) search.set('confidence', params.confidence)
  if (params.issue) search.set('issue', params.issue)
  if (params.source) search.set('source', params.source)
  const query = search.toString()
  return request<ReviewNeighbors>(`/api/reviews/${id}/neighbors${query ? `?${query}` : ''}`)
}

export interface ListReviewsParams {
  q?: string
  state?: string[]
  confidence?: string
  issue?: string
  source?: string
  cursor?: string
  limit?: number
}

export function listReviewBundles(params: ListReviewsParams = {}): Promise<ReviewBundlePage> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.state?.length) search.set('state', params.state.join(','))
  if (params.confidence) search.set('confidence', params.confidence)
  if (params.issue) search.set('issue', params.issue)
  if (params.source) search.set('source', params.source)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  const query = search.toString()
  return request<ReviewBundlePage>(`/api/reviews${query ? `?${query}` : ''}`)
}

export function patchReviewOperationDecisions(
  reviewId: number,
  revisionId: number,
  decisions: ReviewOperationDecision[],
): Promise<ReviewBundleDetail> {
  return request(`/api/reviews/${reviewId}/operations`, {
    method: 'PATCH',
    body: JSON.stringify({ revision_id: revisionId, decisions }),
  })
}

export function applyReviewBundle(reviewId: number): Promise<components['schemas']['ApplyReviewOut']> {
  return request(`/api/reviews/${reviewId}/apply`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
  })
}

export function undoReviewBundle(
  reviewId: number,
  applyRunId: number,
): Promise<components['schemas']['UndoReviewOut']> {
  return request(`/api/reviews/${reviewId}/undo`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey() },
    body: JSON.stringify({ apply_run_id: applyRunId }),
  })
}

export function chooseReviewCover(
  reviewId: number,
  action: 'keep' | 'select' | 'remove',
  assetCandidateId?: number,
): Promise<ReviewBundleDetail> {
  return request(`/api/reviews/${reviewId}/cover`, {
    method: 'POST',
    body: JSON.stringify({ action, ...(assetCandidateId === undefined ? {} : { asset_candidate_id: assetCandidateId }) }),
  })
}

export function uploadReviewCover(reviewId: number, file: File): Promise<components['schemas']['AssetCandidateOut']> {
  return request(`/api/reviews/${reviewId}/cover/candidates`, {
    method: 'POST',
    headers: { 'Content-Type': file.type },
    body: file,
  })
}

export function retryReviewTask(reviewId: number, kind: string): Promise<JobEnqueued> {
  return request(`/api/reviews/${reviewId}/tasks/${encodeURIComponent(kind)}/retry`, { method: 'POST' })
}

export type ReviewOperationEdit =
  | { revision_id: number; kind: 'set_tag'; value: unknown }
  | { revision_id: number; kind: 'write_lyrics'; text: string; synced: boolean }

export function editReviewOperation(
  reviewId: number,
  operationId: number,
  edit: ReviewOperationEdit,
): Promise<ReviewBundleDetail> {
  return request(`/api/reviews/${reviewId}/operations/${operationId}/edit`, {
    method: 'POST',
    body: JSON.stringify(edit),
  })
}

export type ManualCandidateSearchParams = components['schemas']['ManualCandidateSearchRequest']
export type ManualCandidateSearchResult = components['schemas']['ManualCandidateSearchOut']
export type ManualProviderCapability = components['schemas']['ProviderSearchCapabilityOut']

export function getManualCandidateSearchCapabilities(id: number): Promise<ManualProviderCapability[]> {
  return request(`/api/reviews/${id}/candidates/capabilities`)
}

export function searchManualCandidates(
  reviewId: number,
  params: ManualCandidateSearchParams,
): Promise<ManualCandidateSearchResult> {
  return request(`/api/reviews/${reviewId}/candidates/search`, {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export function importManualCandidate(
  reviewId: number,
  source: string,
  refId: string,
): Promise<ReviewBundleDetail> {
  return request(`/api/reviews/${reviewId}/candidates/import`, {
    method: 'POST',
    body: JSON.stringify({ source, ref_id: refId }),
  })
}
