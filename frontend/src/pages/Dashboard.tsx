import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge, EmptyState, SkeletonRows, type BadgeTone } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { ProviderHealthPanel } from '@/components/ProviderHealthPanel'
import { useChangesetList } from '@/hooks/useChangesets'
import { useDashboardSummary } from '@/hooks/useDashboard'
import { useJobList } from '@/hooks/useJobs'
import type { JobState } from '@/lib/types'

// Record<string, ...>, not Record<ChangeSetState, ...>: matches
// ChangesList.tsx's own STATE_TONE, since the state actually observed
// on the wire includes "undo_expired" which isn't in the (incomplete)
// ChangeSetState union in lib/types.ts.
const CHANGESET_STATE_TONE: Record<string, BadgeTone> = {
  draft: 'accent',
  applying: 'accent',
  applied: 'added',
  partially_applied: 'conflict',
  failed: 'removed',
  reverted: 'neutral',
  discarded: 'neutral',
  undo_expired: 'neutral',
}

const JOB_STATE_TONE: Record<JobState, BadgeTone> = {
  pending: 'neutral',
  running: 'accent',
  succeeded: 'added',
  failed: 'removed',
  cancelled: 'conflict',
}

function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex-1 min-w-[140px] p-4 border border-border-subtle rounded-md bg-surface-raised">
      <div className="text-2xl font-semibold font-mono">{value}</div>
      <div className="text-xs text-text-muted mt-2">{label}</div>
    </div>
  )
}

function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex-[1_1_320px] min-w-[300px] border border-border-subtle rounded-md p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold m-0">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  )
}

export function Dashboard() {
  const summary = useDashboardSummary()
  const recentChangesets = useChangesetList()
  const recentJobs = useJobList({ limit: 5 })

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-screen">
      <PageHeader title="Dashboard" />

      <div className="p-5 flex flex-col gap-5">
        {summary.isError ? (
          <EmptyState
            title="Couldn't load library summary"
            description="The server returned an error."
          />
        ) : summary.isLoading || !summary.data ? (
          <div className="flex gap-3 flex-wrap">
            {Array.from({ length: 7 }, (_, i) => (
              <div
                key={i}
                className="flex-1 min-w-[140px] h-[64px] rounded-md bg-surface-raised"
                style={{ animation: 'var(--skeleton-pulse)' }}
              />
            ))}
          </div>
        ) : (
          <div className="flex gap-3 flex-wrap">
            <StatTile label="Tracks" value={summary.data.total_tracks} />
            <StatTile label="Albums" value={summary.data.album_count} />
            <StatTile label="Singles" value={summary.data.singleton_count} />
            <StatTile label="Ungrouped" value={summary.data.ungrouped_track_count} />
            <StatTile label="Probe errors" value={summary.data.tracks_with_errors} />
            <StatTile label="Missing art" value={summary.data.tracks_missing_art} />
            <StatTile label="Missing from disk" value={summary.data.tracks_missing} />
          </div>
        )}

        <div className="flex gap-4 flex-wrap">
          <Panel
            title="Recent changesets"
            action={
              <Link to="/changes" className="text-xs text-accent-text">
                View all
              </Link>
            }
          >
            {recentChangesets.isError ? (
              <span className="text-xs text-text-muted">Couldn't load changesets.</span>
            ) : recentChangesets.isLoading ? (
              <SkeletonRows count={3} />
            ) : !recentChangesets.data || recentChangesets.data.items.length === 0 ? (
              <span className="text-xs text-text-muted">No changesets yet.</span>
            ) : (
              <div className="flex flex-col gap-2">
                {recentChangesets.data.items.slice(0, 5).map((cs) => (
                  <Link
                    key={cs.id}
                    to={`/changes/${cs.id}`}
                    className="flex items-center justify-between gap-3 text-inherit no-underline"
                  >
                    <span className="overflow-hidden text-ellipsis whitespace-nowrap text-sm">
                      #{cs.id} {cs.title}
                    </span>
                    <Badge tone={CHANGESET_STATE_TONE[cs.state] ?? 'neutral'}>{cs.state}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Recent jobs"
            action={
              <Link to="/jobs" className="text-xs text-accent-text">
                View all
              </Link>
            }
          >
            {recentJobs.isError ? (
              <span className="text-xs text-text-muted">Couldn't load jobs.</span>
            ) : recentJobs.isLoading ? (
              <SkeletonRows count={3} />
            ) : !recentJobs.data || recentJobs.data.items.length === 0 ? (
              <span className="text-xs text-text-muted">No jobs yet.</span>
            ) : (
              <div className="flex flex-col gap-2">
                {recentJobs.data.items.map((job) => (
                  <div key={job.id} className="flex items-center justify-between gap-3">
                    <span className="text-sm">
                      #{job.id} {job.type}
                    </span>
                    <Badge tone={JOB_STATE_TONE[job.state]}>{job.state}</Badge>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Provider health">
            <ProviderHealthPanel />
          </Panel>
        </div>
      </div>
    </div>
  )
}
