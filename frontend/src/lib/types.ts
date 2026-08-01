// Server contracts are generated from FastAPI's OpenAPI document.  This module only
// gives existing views stable names and keeps truly local UI types separate.

import type { components } from '@/lib/api-types'

type Schema = components['schemas']

export type TrackSummary = Schema['TrackSummaryOut']
export type TrackDetail = Schema['TrackDetailOut']
export type TrackPage = Schema['TrackPageOut']
export type FacetValue = Schema['FacetValueOut']
export type TrackFacets = Schema['TrackFacetsOut']
export type AuthStatus = Schema['AuthStatusOut']

// View concern: sort is a URL/UI selection rather than an API response model.
export type SortKey = 'title' | 'artist' | 'album' | 'added'

export type InlineSpan = Schema['InlineSpanOut']
export type InlineSpanOp = InlineSpan['op']
export type MultiValueDiff = Schema['MultiValueDiffOut']
export type BinaryDiff = Schema['BinaryDiffOut']
export type FieldDiff = Schema['FieldDiffOut']
export type DiffKind = FieldDiff['kind']

export type ChangeDecisionInput = Schema['ChangeDecisionIn']
export type ChangeDecisionValue = ChangeDecisionInput['decision']
export type Change = Schema['ChangeOut']
export type ChangeApplyState = Change['apply_state']
export type ChangeSetSummary = Schema['ChangeSetSummaryOut']
export type ChangeSetState = ChangeSetSummary['state']
export type ChangeSetEntity = Schema['ChangeSetEntityOut']
export type ChangeSetDetail = Schema['ChangeSetDetailOut']
export type ChangeSetPage = Schema['ChangeSetPageOut']

export type CandidateRow = Schema['CandidateRowOut']
export type MatchProposal = Schema['MatchProposalOut']

export type GroupSummary = Schema['GroupSummaryOut']
export type GroupDetail = Schema['GroupDetailOut']
export type RunCascadeResult = Schema['RunCascadeResultOut']
export type FieldInfo = Schema['FieldInfoOut']

export type JobSummary = Schema['JobSummaryOut']
export type JobDetail = Schema['JobDetailOut']
export type JobPage = Schema['JobPageOut']
export type JobEnqueued = Schema['JobEnqueuedOut']
export type JobState = JobSummary['state']

// SSE has no JSON response model.  This is a local adapter shape for parsed events.
export type JobEventKind = 'progress' | 'log' | 'state'
export interface JobEvent {
  seq: number
  kind: JobEventKind
  payload: Record<string, unknown>
}

export type ImportTask = Schema['ImportTaskOut']
export type ImportSessionSummary = Schema['ImportSessionSummaryOut']
export type ImportSessionDetail = Schema['ImportSessionDetailOut']
export type ImportSessionState = ImportSessionSummary['state']
export type ImportTaskState = ImportTask['state']

export type PathPreviewRow = Schema['PathPreviewRowOut']
export type DuplicateTrack = Schema['DuplicateTrackOut']
export type DuplicateGroup = Schema['DuplicateGroupOut']
export type DuplicateGroupList = Schema['DuplicateGroupListOut']
export type DashboardSummary = Schema['DashboardSummaryOut']
export type ProviderStatus = Schema['ProviderStatusOut']
export type ProviderStatusList = Schema['ProviderStatusListOut']
export type RuntimeCapabilities = Schema['CapabilitiesOut']
export type ProviderSetting = Schema['ProviderSettingOut']
export type TemplateSettings = Schema['TemplateSettingsOut']
export type SettingsSummary = Schema['SettingsSummaryOut']
export type TemplatePreviewResult = Schema['TemplatePreviewOut']

export type LyricsValue = Schema['LyricsValueOut']
export type ReviewBundleDetail = Schema['ReviewBundleDetailOut']
export type ReviewOperation = Schema['ProposalRevisionOut']['operations'][number]
