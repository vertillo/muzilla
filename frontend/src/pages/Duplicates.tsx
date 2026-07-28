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
    <div className="font-sans text-text-primary bg-canvas min-h-screen">
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
        <div className="mt-[6px] text-xs text-text-muted">
          Same recording found at different bitrates, by AcoustID fingerprint match. Detection
          only — nothing is deleted automatically.
        </div>
      </PageHeader>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div className="p-9">
          <EmptyState
            title="Couldn't load duplicate groups"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : groups.length === 0 ? (
        <div className="p-9">
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
              <div className="w-[130px]">
                <Badge tone="accent">{group.basis}</Badge>
              </div>
              <div className="flex-1 min-w-0">
                {group.tracks.map((t) => (
                  <div key={t.id} className="text-sm overflow-hidden text-ellipsis whitespace-nowrap" title={t.path}>
                    {formatTrack(t)}
                  </div>
                ))}
              </div>
              <div className="flex gap-[6px] ml-auto">
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
