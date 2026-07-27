import { useNavigate } from 'react-router-dom'
import { Badge, Button, EmptyState, TableRow } from '@/components/ui'
import { useDetectDuplicates, useDismissDuplicate, useDuplicateGroups } from '@/hooks/useDuplicates'
import { useToasts } from '@/hooks/useToasts'
import type { DuplicateGroup } from '@/lib/types'

function formatTrack(t: DuplicateGroup['tracks'][number]): string {
  const bits = [t.format, t.bitrate ? `${t.bitrate}kbps` : null].filter(Boolean)
  return bits.length ? `${t.path}  [${bits.join(' ')}]` : t.path
}

export function Duplicates() {
  const { data, isLoading } = useDuplicateGroups()
  const dismiss = useDismissDuplicate()
  const detect = useDetectDuplicates()
  const toasts = useToasts()
  const navigate = useNavigate()

  const groups = data?.items ?? []

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: 'var(--space-5)',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          Duplicate tracks
        </h1>
        <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
          Same recording found at different bitrates, by AcoustID fingerprint match. Detection
          only — nothing is deleted automatically.
        </span>
        <div style={{ marginLeft: 'auto' }}>
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
        </div>
        <Button size="sm" variant="ghost" onClick={() => navigate('/jobs')}>
          Jobs
        </Button>
        <Button size="sm" variant="ghost" onClick={() => navigate('/catalog')}>
          Catalog
        </Button>
      </div>

      {isLoading ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState title="Loading duplicate groups…" />
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
