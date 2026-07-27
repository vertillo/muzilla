import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, EmptyState, Select, TableRow } from '@/components/ui'
import { useChangesetList, useUndoChangeset } from '@/hooks/useChangesets'
import { useToasts } from '@/hooks/useToasts'

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
  const { data, isLoading } = useChangesetList(state || undefined)
  const undoMutation = useUndoChangeset()
  const navigate = useNavigate()
  const toasts = useToasts()

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 'var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          Changes
        </h1>
        <div style={{ width: 180 }}>
          <Select value={state} options={STATE_OPTIONS} onChange={setState} />
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <Button variant="ghost" size="sm" onClick={() => navigate('/catalog')}>
            Catalog
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState title="Loading changesets…" />
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
