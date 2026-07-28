import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, EmptyState, ProgressBar, SkeletonRows, TableRow, type BadgeTone } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useCancelJob, useJobList } from '@/hooks/useJobs'
import { useJobEvents } from '@/hooks/useJobEvents'
import { useEnrichArt, useEnrichLyrics, useEnrichReplaygain } from '@/hooks/useEnrichment'
import { useToasts } from '@/hooks/useToasts'
import { ApiError } from '@/lib/api'
import type { JobState, JobSummary } from '@/lib/types'

const STATE_TONE: Record<JobState, BadgeTone> = {
  pending: 'neutral',
  running: 'accent',
  succeeded: 'added',
  failed: 'removed',
  cancelled: 'conflict',
}

function jobProgressPercent(job: JobSummary): number | null {
  if (job.progress_total === null || job.progress_total === 0) return null
  return Math.round((job.progress_current / job.progress_total) * 100)
}

function JobDetailPanel({ job }: { job: JobSummary }) {
  // Subscribing works for completed jobs too — GET .../events?after=0
  // replays every persisted job_events row, then immediately emits
  // "done", so a finished job's history still renders here rather
  // than showing an empty panel.
  const jobEvents = useJobEvents(job.id)
  const cancelJob = useCancelJob()
  const isActive = job.state === 'pending' || job.state === 'running'

  return (
    <div
      style={{
        padding: 'var(--space-4) var(--space-5)',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-surface-raised)',
      }}
    >
      {isActive && jobEvents.latestProgress && (
        <ProgressBar
          value={
            jobEvents.latestProgress.total
              ? Math.round((jobEvents.latestProgress.current / jobEvents.latestProgress.total) * 100)
              : 0
          }
          label={jobEvents.latestProgress.message ?? job.type}
        />
      )}
      <div style={{ marginTop: 'var(--space-3)', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {jobEvents.events.length === 0 ? (
          <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>No log events.</span>
        ) : (
          jobEvents.events
            .filter((e) => e.kind === 'log')
            .map((e) => (
              <div
                key={e.seq}
                style={{ fontSize: 'var(--text-xs-size)', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}
              >
                {String(e.payload.message ?? '')}
              </div>
            ))
        )}
      </div>
      {isActive && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Button size="sm" variant="ghost" disabled={cancelJob.isPending} onClick={() => cancelJob.mutate(job.id)}>
            Cancel
          </Button>
        </div>
      )}
      {job.error && (
        <div style={{ marginTop: 'var(--space-3)', color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
          {job.error}
        </div>
      )}
    </div>
  )
}

export function Jobs() {
  const { data, isLoading, isError, error, refetch } = useJobList()
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const navigate = useNavigate()
  const toasts = useToasts()

  const enrichReplaygain = useEnrichReplaygain()
  const enrichArt = useEnrichArt()
  const enrichLyrics = useEnrichLyrics()

  function queueEnrichment(label: string, mutate: ReturnType<typeof useEnrichReplaygain>['mutate']) {
    mutate(undefined, {
      onSuccess: (job) =>
        toasts.push({ tone: 'info', title: `${label} queued (job #${job.job_id})` }),
    })
  }

  const jobs = data?.items ?? []

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader
        title="Jobs"
        actions={
          <Button size="sm" variant="secondary" onClick={() => navigate('/import')}>
            New import
          </Button>
        }
      />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: 'var(--space-3) var(--space-5)',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>Enrichment:</span>
        <Button
          size="sm"
          variant="ghost"
          disabled={enrichReplaygain.isPending}
          onClick={() => queueEnrichment('ReplayGain', enrichReplaygain.mutate)}
        >
          ReplayGain
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={enrichArt.isPending}
          onClick={() => queueEnrichment('Album art', enrichArt.mutate)}
        >
          Album art
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={enrichLyrics.isPending}
          onClick={() => queueEnrichment('Lyrics', enrichLyrics.mutate)}
        >
          Lyrics
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="Couldn't load jobs"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : jobs.length === 0 ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="No jobs yet"
            description="Scans, imports, and applies all run as background jobs — start an import to see one here."
            action={<Button onClick={() => navigate('/import')}>Start an import</Button>}
          />
        </div>
      ) : (
        <div>
          {jobs.map((job) => {
            const percent = jobProgressPercent(job)
            const isExpanded = expandedId === job.id
            return (
              <div key={job.id}>
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  onClick={() => setExpandedId(isExpanded ? null : job.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') setExpandedId(isExpanded ? null : job.id)
                  }}
                  className="focus-ring"
                  style={{ cursor: 'pointer' }}
                >
                  <TableRow>
                    <div style={{ width: 60, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                      #{job.id}
                    </div>
                    <div style={{ width: 130 }}>{job.type}</div>
                    <div style={{ width: 110 }}>
                      <Badge tone={STATE_TONE[job.state]}>{job.state}</Badge>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {percent !== null ? (
                        <ProgressBar value={percent} />
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs-size)' }}>
                          {job.progress_message ?? ''}
                        </span>
                      )}
                    </div>
                    <div style={{ width: 24, color: 'var(--text-muted)' }}>{isExpanded ? '▾' : '▸'}</div>
                  </TableRow>
                </div>
                {isExpanded && <JobDetailPanel job={job} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
