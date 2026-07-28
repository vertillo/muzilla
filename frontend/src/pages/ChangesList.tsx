import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, EmptyState, Select, SkeletonRows, TableRow } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useChangesetList, useUndoChangeset } from '@/hooks/useChangesets'
import { useToasts } from '@/hooks/useToasts'
import { ApiError } from '@/lib/api'

const STATE_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'applied', label: 'Applied' },
  { value: 'partially_applied', label: 'Partially applied' },
  { value: 'failed', label: 'Failed' },
  { value: 'reverted', label: 'Reverted' },
  { value: 'undo_expired', label: 'Undo expired' },
]

const STATE_TONE: Record<string, 'accent' | 'added' | 'conflict' | 'removed' | 'neutral'> = {
  draft: 'accent',
  applied: 'added',
  partially_applied: 'conflict',
  failed: 'removed',
  reverted: 'neutral',
  discarded: 'neutral',
  applying: 'accent',
  undo_expired: 'neutral',
}

export function ChangesList() {
  const [state, setState] = useState('')
  const { data, isLoading, isError, error, refetch } = useChangesetList(state || undefined)
  const undoMutation = useUndoChangeset()
  const navigate = useNavigate()
  const toasts = useToasts()

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-screen">
      <PageHeader title="Changes">
        <div className="mt-3 w-[180px]">
          <Select value={state} options={STATE_OPTIONS} onChange={setState} />
        </div>
      </PageHeader>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div className="p-9">
          <EmptyState
            title="Couldn't load changesets"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="p-9">
          <EmptyState title="No changesets" description="Edit a track or run the grouping cascade to create one." />
        </div>
      ) : (
        <div>
          {data.items.map((cs) => (
            <TableRow key={cs.id}>
              <div className="w-[50px] font-mono text-text-muted">#{cs.id}</div>
              <div className="w-[120px]">
                <Badge tone={STATE_TONE[cs.state] ?? 'neutral'}>{cs.state}</Badge>
              </div>
              <div className="w-[160px] text-text-secondary">{cs.source}</div>
              <div className="flex-1 min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                {cs.title}
              </div>
              <div className="w-[100px] text-text-secondary font-mono">
                {cs.stats.accepted ?? 0}/{cs.stats.total ?? 0}
              </div>
              <div className="flex gap-[6px]">
                <Button size="sm" variant="ghost" onClick={() => navigate(`/changes/${cs.id}`)}>
                  Review
                </Button>
                {(cs.state === 'applied' || cs.state === 'partially_applied') && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={undoMutation.isPending}
                    onClick={() =>
                      undoMutation.mutate(cs.id, {
                        onSuccess: (job) =>
                          toasts.push({
                            tone: 'info',
                            title: `Undo queued (job #${job.job_id})`,
                            description: 'Open the changeset to track progress.',
                          }),
                      })
                    }
                  >
                    Undo
                  </Button>
                )}
              </div>
            </TableRow>
          ))}
        </div>
      )}
    </div>
  )
}
