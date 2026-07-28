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
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader title="Changes">
        <div style={{ marginTop: 8, width: 180 }}>
          <Select value={state} options={STATE_OPTIONS} onChange={setState} />
        </div>
      </PageHeader>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="Couldn't load changesets"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState title="No changesets" description="Edit a track or run the grouping cascade to create one." />
        </div>
      ) : (
        <div>
          {data.items.map((cs) => (
            <TableRow key={cs.id}>
              <div style={{ width: 50, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>#{cs.id}</div>
              <div style={{ width: 120 }}>
                <Badge tone={STATE_TONE[cs.state] ?? 'neutral'}>{cs.state}</Badge>
              </div>
              <div style={{ width: 160, color: 'var(--text-secondary)' }}>{cs.source}</div>
              <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {cs.title}
              </div>
              <div style={{ width: 100, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
                {cs.stats.accepted ?? 0}/{cs.stats.total ?? 0}
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
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
