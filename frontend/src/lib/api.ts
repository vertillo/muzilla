import type {
  AuthStatus,
  ChangeDecisionInput,
  ChangeSetDetail,
  ChangeSetPage,
  FieldInfo,
  GroupDetail,
  GroupSummary,
  ImportSessionDetail,
  ImportSessionSummary,
  JobDetail,
  JobEnqueued,
  JobPage,
  MatchProposal,
  PathPreviewRow,
  RunCascadeResult,
  TrackDetail,
  TrackPage,
} from '@/lib/types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

/** Generates a v4-shaped random id for the Idempotency-Key header on
 * mutating endpoints (docs/PLAN.md §10) — good enough uniqueness for a
 * client-generated retry key, no crypto requirement. */
function idempotencyKey(): string {
  return crypto.randomUUID()
}

export interface ListTracksParams {
  q?: string
  sort?: string
  cursor?: string
  limit?: number
}

export function listTracks(params: ListTracksParams): Promise<TrackPage> {
  const search = new URLSearchParams()
  if (params.q) search.set('q', params.q)
  if (params.sort) search.set('sort', params.sort)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  const qs = search.toString()
  return request<TrackPage>(`/api/tracks${qs ? `?${qs}` : ''}`)
}

export function getTrack(id: number): Promise<TrackDetail> {
  return request<TrackDetail>(`/api/tracks/${id}`)
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

export function listFields(): Promise<{ items: FieldInfo[] }> {
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

export interface BulkEditField {
  field: string
  new_value: unknown
}

export function bulkEditTracks(trackIds: number[], fields: BulkEditField[]): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>('/api/tracks/bulk-edit', {
    method: 'POST',
    body: JSON.stringify({ track_ids: trackIds, fields }),
  })
}

export interface FindReplaceParams {
  track_ids: number[]
  field: string
  find: string
  replace: string
  use_regex?: boolean
}

export function previewFindReplace(
  params: FindReplaceParams,
): Promise<{ rows: { track_id: number; old_value: string; new_value: string }[] }> {
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

// --- groups --------------------------------------------------------------

export function listGroups(): Promise<{ items: GroupSummary[] }> {
  return request('/api/groups')
}

export function getGroup(id: number): Promise<GroupDetail> {
  return request<GroupDetail>(`/api/groups/${id}`)
}

export function runGroupingCascade(): Promise<RunCascadeResult> {
  return request<RunCascadeResult>('/api/groups/cascade', { method: 'POST' })
}

export function mergeGroups(intoGroupId: number, fromGroupIds: number[]): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/groups/${intoGroupId}/merge`, {
    method: 'POST',
    body: JSON.stringify({ from_group_ids: fromGroupIds }),
  })
}

export function splitGroup(groupId: number, trackIds: number[]): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/groups/${groupId}/split`, {
    method: 'POST',
    body: JSON.stringify({ track_ids: trackIds }),
  })
}

export function reassignTrack(trackId: number, toGroupId: number): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/groups/${toGroupId}/reassign`, {
    method: 'POST',
    body: JSON.stringify({ track_id: trackId, to_group_id: toGroupId }),
  })
}

export function forceToSingleton(trackId: number): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>('/api/groups/force-singleton', {
    method: 'POST',
    body: JSON.stringify({ track_id: trackId }),
  })
}

export function pinGroup(groupId: number): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/groups/${groupId}/pin`, { method: 'POST' })
}

// --- matching --------------------------------------------------------------

export function getGroupCandidates(groupId: number): Promise<MatchProposal> {
  return request<MatchProposal>(`/api/groups/${groupId}/candidates`)
}

export function stageGroupMatch(
  groupId: number,
  source: string,
  refId: string,
): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/api/groups/${groupId}/stage`, {
    method: 'POST',
    body: JSON.stringify({ source, ref_id: refId }),
  })
}

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

// --- jobs --------------------------------------------------------------

export interface ListJobsParams {
  state?: string
  cursor?: string
  limit?: number
}

export function listJobs(params: ListJobsParams = {}): Promise<JobPage> {
  const search = new URLSearchParams()
  if (params.state) search.set('state', params.state)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  const qs = search.toString()
  return request<JobPage>(`/api/jobs${qs ? `?${qs}` : ''}`)
}

export function getJob(id: number): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${id}`)
}

export function cancelJob(id: number): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${id}/cancel`, { method: 'POST' })
}

// --- imports -------------------------------------------------------------

export function startScan(root: string): Promise<JobEnqueued> {
  return request<JobEnqueued>('/api/scan', {
    method: 'POST',
    body: JSON.stringify({ root }),
  })
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

export function resumeImport(id: number): Promise<ImportSessionSummary> {
  return request<ImportSessionSummary>(`/api/imports/${id}/resume`, { method: 'POST' })
}

// --- paths -----------------------------------------------------------------

export interface PathPreviewParams {
  track_ids?: number[]
  group_id?: number
  template?: string
}

export function previewPaths(params: PathPreviewParams): Promise<{ rows: PathPreviewRow[] }> {
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
