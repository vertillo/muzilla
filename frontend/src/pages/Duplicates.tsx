import { Badge, Button, EmptyState, SkeletonRows, TableRow } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useDetectDuplicates, useDismissDuplicate, useDuplicateGroups } from '@/hooks/useDuplicates'
import { useToasts } from '@/hooks/useToasts'
import { ApiError } from '@/lib/api'
import type { DuplicateGroup } from '@/lib/types'

function formatTrack(t: DuplicateGroup['tracks'][number]): string {
  const bits = [t.format, t.bitrate ? `${t.bitrate}kbps` : null].filter(Boolean)
  return bits.length ? `${t.path}  [${bits.join(' ')}]` : t.path
}

export function Duplicates() {
  const { data, isLoading, isError, error, refetch } = useDuplicateGroups()
  const dismiss = useDismissDuplicate()
  const detect = useDetectDuplicates()
  const toasts = useToasts()

  const groups = data?.items ?? []

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader
        title="Duplicate tracks"
        actions={
          <Button
            size="sm"
            variant="secondary"
            disabled={detect.isPending}
            onClick={() =>
              detect.mutate(undefined, {
                onSuccess: (job) =>
                  toasts.push({
                    tone: 'info',
                    title: `Duplicate scan queued (job #${job.job_id})`,
                    description: 'Open Jobs to track progress; refresh this page once it finishes.',
                  }),
              })
            }
          >
            {detect.isPending ? 'Queuing…' : 'Scan for duplicates'}
          </Button>
        }
      >
        <div style={{ marginTop: 6, fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
          Same recording found at different bitrates, by AcoustID fingerprint match. Detection
          only — nothing is deleted automatically.
        </div>
      </PageHeader>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="Couldn't load duplicate groups"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : groups.length === 0 ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="No duplicates found"
            description="Run a scan to check for the same recording at different bitrates. This relies on fingerprint data from Phase 3, so tracks that haven't been fingerprinted yet won't be matched."
            action={
              <Button disabled={detect.isPending} onClick={() => detect.mutate()}>
                {detect.isPending ? 'Queuing…' : 'Scan for duplicates'}
              </Button>
            }
          />
        </div>
      ) : (
        <div>
          {groups.map((group) => (
            <TableRow key={group.id}>
              <div style={{ width: 130 }}>
                <Badge tone="accent">{group.basis}</Badge>
              </div>
              <div style={{ flex: '1 1 0', minWidth: 0 }}>
                {group.tracks.map((t) => (
                  <div
                    key={t.id}
                    style={{
                      fontSize: 'var(--text-sm-size)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={t.path}
                  >
                    {formatTrack(t)}
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                <Button size="sm" variant="ghost" disabled={dismiss.isPending} onClick={() => dismiss.mutate(group.id)}>
                  Dismiss
                </Button>
              </div>
            </TableRow>
          ))}
        </div>
      )}
    </div>
  )
}
