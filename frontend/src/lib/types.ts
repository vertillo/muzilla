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

export type CandidateRow = Schema['CandidateRowOut']
export type MatchProposal = Schema['MatchProposalOut']

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
export type ReviewBundleSummary = Schema['ReviewBundleSummaryOut']
export type ReviewBundlePage = Schema['ReviewBundlePageOut']
export type ReviewNeighbors = Schema['ReviewNeighborsOut']
export type ReviewOperationDecision = Schema['ReviewOperationDecisionIn']
export type ReviewOperation = Schema['ProposalRevisionOut']['operations'][number]
export type ManualCandidateSearchResult = Schema['ManualCandidateSearchOut']
export type ManualProviderCapability = Schema['ProviderSearchCapabilityOut']
