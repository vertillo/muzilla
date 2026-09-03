import { useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, EmptyState, ProgressBar, TableRow, type BadgeTone } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useImportSession, useResumeImport } from '@/hooks/useImports'
import { useCancelJob } from '@/hooks/useJobs'
import { useJobEvents } from '@/hooks/useJobEvents'
import type { ImportTaskState } from '@/lib/types'

const TASK_TONE: Record<ImportTaskState, BadgeTone> = {
  pending: 'neutral',
  running: 'accent',
  done: 'added',
  failed: 'removed',
  skipped: 'neutral',
  cancelled: 'neutral',
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
  const cancelJob = useCancelJob()

  const isRunningJob = session?.job_id !== null && session?.state !== 'reviewing' &&
    session?.state !== 'completed' && session?.state !== 'failed' && session?.state !== 'cancelled'
  const canCancel = isRunningJob && session?.job_id !== null
  const jobEvents = useJobEvents(isRunningJob ? (session?.job_id ?? null) : null)

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
          <div className="flex items-center gap-2">
            {canCancel && (
              <Button size="sm" variant="ghost" disabled={cancelJob.isPending} onClick={() => session.job_id && cancelJob.mutate(session.job_id)}>
                Annulla import
              </Button>
            )}
            {canResume && (
              <Button size="sm" variant="secondary" disabled={resumeImport.isPending} onClick={() => resumeImport.mutate()}>
                Resume
              </Button>
            )}
          </div>
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
        {canCancel && (
          <div className="mt-3 text-xs text-text-muted">
            Annullamento cooperativo: i file già indicizzati restano nel catalogo; nessuna proposta verrà pubblicata dopo il checkpoint. Puoi annullare in qualsiasi momento.
          </div>
        )}
        {session.state === 'cancelled' && (
          <div className="mt-3 p-3 rounded-md bg-surface-raised border border-border-subtle text-sm text-text-secondary" role="status">
            Import annullato — i file già indicizzati restano validi nel catalogo; le attività non terminate sono segnate annullate; nessuna proposta è stata pubblicata dopo il checkpoint di cancellazione. Puoi riprendere questo import o avviarne uno nuovo con un perimetro diverso.
          </div>
        )}
      </div>

      <div className="p-5">
        <h2 className="text-md font-semibold mt-0">
          Revisioni ({session.review_bundle_ids.length})
        </h2>
        {session.review_bundle_ids.length === 0 ? (
          <EmptyState
            title={session.state === 'reviewing' || session.state === 'completed' ? 'Nessuna revisione creata' : 'Corrispondenze in preparazione'}
            description={
              session.state === 'reviewing' || session.state === 'completed'
                ? 'L’import non ha ancora creato elementi da controllare.'
                : 'Le revisioni compaiono qui mentre il matching prepara ogni file o raccolta.'
            }
          />
        ) : (
          <div>
            {session.review_bundle_ids.map((id) => {
              return (
                <TableRow key={id}>
                  <div className="w-[60px] font-mono text-text-muted">#{id}</div>
                  <div className="flex-1 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                    Revisione pronta o in preparazione
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/reviews/${id}`)}>
                    Apri
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
