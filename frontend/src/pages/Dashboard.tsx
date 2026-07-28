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
    <div
      style={{
        flex: 1,
        minWidth: 140,
        padding: 'var(--space-4)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-surface-raised)',
      }}
    >
      <div style={{ fontSize: 'var(--text-2xl-size)', fontWeight: 'var(--font-weight-semibold)', fontFamily: 'var(--font-mono)' }}>
        {value}
      </div>
      <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)', marginTop: 4 }}>{label}</div>
    </div>
  )
}

function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div
      style={{
        flex: '1 1 320px',
        minWidth: 300,
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        padding: 'var(--space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-3)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ fontSize: 'var(--text-sm-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          {title}
        </h2>
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
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader title="Dashboard" />

      <div style={{ padding: 'var(--space-5)', display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
        {summary.isError ? (
          <EmptyState
            title="Couldn't load library summary"
            description="The server returned an error."
          />
        ) : summary.isLoading || !summary.data ? (
          <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
            {Array.from({ length: 7 }, (_, i) => (
              <div
                key={i}
                style={{
                  flex: 1,
                  minWidth: 140,
                  height: 64,
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--bg-surface-raised)',
                  animation: 'var(--skeleton-pulse)',
                }}
              />
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
            <StatTile label="Tracks" value={summary.data.total_tracks} />
            <StatTile label="Albums" value={summary.data.album_count} />
            <StatTile label="Singles" value={summary.data.singleton_count} />
            <StatTile label="Ungrouped" value={summary.data.ungrouped_track_count} />
            <StatTile label="Probe errors" value={summary.data.tracks_with_errors} />
            <StatTile label="Missing art" value={summary.data.tracks_missing_art} />
            <StatTile label="Missing from disk" value={summary.data.tracks_missing} />
          </div>
        )}

        <div style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
          <Panel title="Recent changesets" action={<Link to="/changes" style={{ fontSize: 'var(--text-xs-size)', color: 'var(--accent-text)' }}>View all</Link>}>
            {recentChangesets.isError ? (
              <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
                Couldn't load changesets.
              </span>
            ) : recentChangesets.isLoading ? (
              <SkeletonRows count={3} />
            ) : !recentChangesets.data || recentChangesets.data.items.length === 0 ? (
              <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
                No changesets yet.
              </span>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                {recentChangesets.data.items.slice(0, 5).map((cs) => (
                  <Link
                    key={cs.id}
                    to={`/changes/${cs.id}`}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 'var(--space-3)',
                      color: 'inherit',
                      textDecoration: 'none',
                    }}
                  >
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 'var(--text-sm-size)' }}>
                      #{cs.id} {cs.title}
                    </span>
                    <Badge tone={CHANGESET_STATE_TONE[cs.state] ?? 'neutral'}>{cs.state}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Recent jobs" action={<Link to="/jobs" style={{ fontSize: 'var(--text-xs-size)', color: 'var(--accent-text)' }}>View all</Link>}>
            {recentJobs.isError ? (
              <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
                Couldn't load jobs.
              </span>
            ) : recentJobs.isLoading ? (
              <SkeletonRows count={3} />
            ) : !recentJobs.data || recentJobs.data.items.length === 0 ? (
              <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>No jobs yet.</span>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                {recentJobs.data.items.map((job) => (
                  <div key={job.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-3)' }}>
                    <span style={{ fontSize: 'var(--text-sm-size)' }}>
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
