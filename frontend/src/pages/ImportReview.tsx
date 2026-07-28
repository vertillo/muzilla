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
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Loading import session…" />
      </div>
    )
  }

  if (!session) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState
          title="Import session not found"
          action={<Button onClick={() => navigate('/import')}>Start a new import</Button>}
        />
      </div>
    )
  }

  const canResume = session.state === 'failed' || session.state === 'cancelled'

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
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
        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 'var(--text-sm-size)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
            {session.library_root}
          </span>
          <Badge tone={session.state === 'failed' ? 'removed' : session.state === 'completed' ? 'added' : 'accent'}>
            {session.state}
          </Badge>
        </div>
      </PageHeader>

      {session.error && (
        <div style={{ padding: 'var(--space-4) var(--space-5)', color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
          {session.error}
        </div>
      )}

      <div style={{ padding: 'var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-5)' }}>
          {session.tasks.map((task) => (
            <div key={task.stage} style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <Badge tone={TASK_TONE[task.state]}>{task.state}</Badge>
                <span style={{ fontSize: 'var(--text-sm-size)', fontWeight: 'var(--font-weight-medium)' }}>
                  {STAGE_LABEL[task.stage] ?? task.stage}
                </span>
              </div>
              {task.error && (
                <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--diff-removed)' }}>{task.error}</div>
              )}
            </div>
          ))}
        </div>
        {isRunningJob && jobEvents.latestProgress && (
          <div style={{ marginTop: 'var(--space-4)', maxWidth: 400 }}>
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

      <div style={{ padding: 'var(--space-5)' }}>
        <h2 style={{ fontSize: 'var(--text-md-size)', fontWeight: 'var(--font-weight-semibold)', marginTop: 0 }}>
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
                  <div style={{ width: 60, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>#{id}</div>
                  <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {q.data?.title ?? '…'}
                  </div>
                  <div style={{ width: 120 }}>
                    {q.data && <Badge tone="neutral">{q.data.state}</Badge>}
                  </div>
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
