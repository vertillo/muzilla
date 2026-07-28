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
    <div className="py-4 px-5 border-b border-border-subtle bg-surface-raised">
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
      <div className="mt-3 flex flex-col gap-[4px]">
        {jobEvents.events.length === 0 ? (
          <span className="text-xs text-text-muted">No log events.</span>
        ) : (
          jobEvents.events
            .filter((e) => e.kind === 'log')
            .map((e) => (
              <div key={e.seq} className="text-xs font-mono text-text-secondary">
                {String(e.payload.message ?? '')}
              </div>
            ))
        )}
      </div>
      {isActive && (
        <div className="mt-3">
          <Button size="sm" variant="ghost" disabled={cancelJob.isPending} onClick={() => cancelJob.mutate(job.id)}>
            Cancel
          </Button>
        </div>
      )}
      {job.error && (
        <div className="mt-3 text-sm" style={{ color: 'var(--diff-removed)' }}>
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
    <div className="font-sans text-text-primary bg-canvas min-h-screen">
      <PageHeader
        title="Jobs"
        actions={
          <Button size="sm" variant="secondary" onClick={() => navigate('/import')}>
            New import
          </Button>
        }
      />

      <div className="flex items-center gap-3 py-3 px-5 border-b border-border-subtle">
        <span className="text-xs text-text-muted">Enrichment:</span>
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
        <div className="p-9">
          <EmptyState
            title="Couldn't load jobs"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : jobs.length === 0 ? (
        <div className="p-9">
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
                  className="focus-ring cursor-pointer"
                >
                  <TableRow>
                    <div className="w-[60px] font-mono text-text-muted">#{job.id}</div>
                    <div className="w-[130px]">{job.type}</div>
                    <div className="w-[110px]">
                      <Badge tone={STATE_TONE[job.state]}>{job.state}</Badge>
                    </div>
                    <div className="flex-1 min-w-0">
                      {percent !== null ? (
                        <ProgressBar value={percent} />
                      ) : (
                        <span className="text-text-muted text-xs">{job.progress_message ?? ''}</span>
                      )}
                    </div>
                    <div className="w-7 text-text-muted">{isExpanded ? '▾' : '▸'}</div>
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
