import { useNavigate, useParams } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { Badge, Button, EmptyState, ProgressBar, TableRow, type BadgeTone } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useImportSession, useResumeImport } from '@/hooks/useImports'
import { useJobEvents } from '@/hooks/useJobEvents'
import { getChangeset } from '@/lib/api'
import type { ImportTaskState } from '@/lib/types'

const TASK_TONE: Record<ImportTaskState, BadgeTone> = {
  pending: 'neutral',
  running: 'accent',
  done: 'added',
  failed: 'removed',
  skipped: 'neutral',
}

const STAGE_LABEL: Record<string, string> = {
  scan: 'Scan',
  fingerprint: 'Fingerprint',
  group: 'Group',
  match: 'Match',
}

export function ImportReview() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const importSessionId = sessionId ? Number(sessionId) : NaN
  const navigate = useNavigate()

  const { data: session, isLoading } = useImportSession(
    Number.isFinite(importSessionId) ? importSessionId : null,
  )
  const resumeImport = useResumeImport(importSessionId)

  const isRunningJob = session?.job_id !== null && session?.state !== 'reviewing' &&
    session?.state !== 'completed' && session?.state !== 'failed' && session?.state !== 'cancelled'
  const jobEvents = useJobEvents(isRunningJob ? (session?.job_id ?? null) : null)

  const changesetQueries = useQueries({
    queries: (session?.changeset_ids ?? []).map((id) => ({
      queryKey: ['changeset', id],
      queryFn: () => getChangeset(id),
    })),
  })

  if (isLoading) {
    return (
      <div className="p-9">
        <EmptyState title="Loading import session…" />
      </div>
    )
  }

  if (!session) {
    return (
      <div className="p-9">
        <EmptyState
          title="Import session not found"
          action={<Button onClick={() => navigate('/import')}>Start a new import</Button>}
        />
      </div>
    )
  }

  const canResume = session.state === 'failed' || session.state === 'cancelled'

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader
        title={`Import #${session.id}`}
        breadcrumb={{ label: 'Import', to: '/import' }}
        actions={
          canResume && (
            <Button size="sm" variant="secondary" disabled={resumeImport.isPending} onClick={() => resumeImport.mutate()}>
              Resume
            </Button>
          )
        }
      >
        <div className="mt-[6px] flex items-center gap-4">
          <span className="text-sm text-text-secondary font-mono">{session.library_root}</span>
          <Badge tone={session.state === 'failed' ? 'removed' : session.state === 'completed' ? 'added' : 'accent'}>
            {session.state}
          </Badge>
        </div>
      </PageHeader>

      {session.error && (
        <div className="py-4 px-5 text-sm text-diff-removed">
          {session.error}
        </div>
      )}

      <div className="p-5 border-b border-border-subtle">
        <div className="flex gap-5">
          {session.tasks.map((task) => (
            <div key={task.stage} className="flex-1">
              <div className="flex items-center gap-[6px] mb-[6px]">
                <Badge tone={TASK_TONE[task.state]}>{task.state}</Badge>
                <span className="text-sm font-medium">{STAGE_LABEL[task.stage] ?? task.stage}</span>
              </div>
              {task.error && (
                <div className="text-xs text-diff-removed">
                  {task.error}
                </div>
              )}
            </div>
          ))}
        </div>
        {isRunningJob && jobEvents.latestProgress && (
          <div className="mt-4 max-w-[400px]">
            <ProgressBar
              value={
                jobEvents.latestProgress.total
                  ? Math.round((jobEvents.latestProgress.current / jobEvents.latestProgress.total) * 100)
                  : 0
              }
              label={jobEvents.latestProgress.message ?? 'Importing…'}
            />
          </div>
        )}
      </div>

      <div className="p-5">
        <h2 className="text-md font-semibold mt-0">
          Proposed changesets ({session.changeset_ids.length})
        </h2>
        {session.changeset_ids.length === 0 ? (
          <EmptyState
            title={session.state === 'reviewing' || session.state === 'completed' ? 'Nothing to review' : 'Matching not finished yet'}
            description={
              session.state === 'reviewing' || session.state === 'completed'
                ? 'No candidates were found for any group — nothing was auto-staged.'
                : 'Changesets will appear here once the match stage runs.'
            }
          />
        ) : (
          <div>
            {changesetQueries.map((q, i) => {
              const id = session.changeset_ids[i]
              return (
                <TableRow key={id}>
                  <div className="w-[60px] font-mono text-text-muted">#{id}</div>
                  <div className="flex-1 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                    {q.data?.title ?? '…'}
                  </div>
                  <div className="w-[120px]">{q.data && <Badge tone="neutral">{q.data.state}</Badge>}</div>
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/changes/${id}`)}>
                    Review
                  </Button>
                </TableRow>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
