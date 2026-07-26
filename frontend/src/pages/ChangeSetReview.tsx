import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Badge, Button, ConfidenceBar, EmptyState, ProgressBar, ThreeStateToggle, type ToggleValue } from '@/components/ui'
import { InlineDiff } from '@/components/InlineDiff'
import { CandidatePicker } from '@/components/CandidatePicker'
import {
  useApplyChangeset,
  useChangeset,
  usePatchDecisions,
  useUndoChangeset,
} from '@/hooks/useChangesets'
import { useJobEvents } from '@/hooks/useJobEvents'
import { useToasts } from '@/hooks/useToasts'
import { getJob } from '@/lib/api'
import type { Change, ChangeDecisionValue } from '@/lib/types'

// docs/PLAN.md §9: ThreeStateToggle.d.ts's accept|pending|reject is
// reconciled with changes.decision's pending|accepted|rejected here —
// the fourth state ("edited") is represented by Change.is_manual
// rather than a fourth toggle position, shown as a separate badge.
function decisionToToggle(decision: ChangeDecisionValue): ToggleValue {
  if (decision === 'accepted') return 'accept'
  if (decision === 'rejected') return 'reject'
  return 'pending'
}

function toggleToDecision(value: ToggleValue): ChangeDecisionValue {
  if (value === 'accept') return 'accepted'
  if (value === 'reject') return 'rejected'
  return 'pending'
}

/** Groups changes by entity_id, preserving first-seen order — the left
 * pane's per-entity rollup (docs/PLAN.md §9: "entity list with per-
 * track change counts"). */
function groupByEntity(changes: Change[]): { entityId: number; changes: Change[] }[] {
  const order: number[] = []
  const byId = new Map<number, Change[]>()
  for (const c of changes) {
    if (!byId.has(c.entity_id)) {
      byId.set(c.entity_id, [])
      order.push(c.entity_id)
    }
    byId.get(c.entity_id)!.push(c)
  }
  return order.map((id) => ({ entityId: id, changes: byId.get(id)! }))
}

function entityChipState(changes: Change[]): 'conflict' | 'rejected' | 'accepted' | 'mixed' | 'pending' {
  // Conflict > Rejected > Accepted > Mixed, matching the ported
  // trackChip() precedence from the Change Review.dc.html prototype.
  if (changes.some((c) => c.apply_state === 'conflicted')) return 'conflict'
  const decisions = new Set(changes.map((c) => c.decision))
  if (decisions.size === 1) {
    const only = [...decisions][0]
    if (only === 'rejected') return 'rejected'
    if (only === 'accepted') return 'accepted'
    return 'pending'
  }
  if (decisions.has('rejected') && !decisions.has('accepted') && !decisions.has('pending')) return 'rejected'
  return 'mixed'
}

const CHIP_TONE = {
  conflict: 'conflict',
  rejected: 'removed',
  accepted: 'added',
  mixed: 'accent',
  pending: 'neutral',
} as const

export function ChangeSetReview() {
  const { id } = useParams<{ id: string }>()
  const changeSetId = id ? Number(id) : NaN
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toasts = useToasts()

  const { data: cs, isLoading } = useChangeset(Number.isFinite(changeSetId) ? changeSetId : null)
  const patchDecisions = usePatchDecisions(changeSetId)
  const applyMutation = useApplyChangeset()
  const undoMutation = useUndoChangeset()

  // Apply/undo enqueue a job and return immediately (docs/PLAN.md §10:
  // `POST .../apply -> 202 {job_id}`) — track which job (if any) is
  // in flight and which action it represents, then subscribe via SSE.
  const [activeJob, setActiveJob] = useState<{ id: number; action: 'apply' | 'undo' } | null>(null)
  const jobEvents = useJobEvents(activeJob?.id ?? null)

  useEffect(() => {
    if (!activeJob || !jobEvents.isComplete) return

    queryClient.invalidateQueries({ queryKey: ['changeset', changeSetId] })
    queryClient.invalidateQueries({ queryKey: ['changesets'] })
    queryClient.invalidateQueries({ queryKey: ['tracks'] })

    if (jobEvents.terminalState === 'succeeded') {
      if (activeJob.action === 'apply') {
        toasts.push({ tone: 'success', title: `Changeset #${changeSetId} applied` })
      } else {
        // The undo changeset's id is on the job's `result`, not any SSE
        // event payload (SSE's "done" frame only carries the terminal
        // state) — fetch the job detail once to read it.
        toasts.push({ tone: 'success', title: 'Undo staged' })
        void getJob(activeJob.id).then((detail) => {
          const undoChangeSetId = detail.result?.undo_change_set_id
          if (typeof undoChangeSetId === 'number') navigate(`/changes/${undoChangeSetId}`)
        })
      }
    } else {
      toasts.push({
        tone: 'error',
        title: `${activeJob.action === 'apply' ? 'Apply' : 'Undo'} ${jobEvents.terminalState ?? 'failed'}`,
        description: jobEvents.error ?? undefined,
      })
    }
    setActiveJob(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobEvents.isComplete])

  function runApply() {
    applyMutation.mutate(changeSetId, {
      onSuccess: (data) => setActiveJob({ id: data.job_id, action: 'apply' }),
    })
  }

  function runUndo() {
    undoMutation.mutate(changeSetId, {
      onSuccess: (data) => setActiveJob({ id: data.job_id, action: 'undo' }),
    })
  }

  const [selectedEntityId, setSelectedEntityId] = useState<number | null>(null)
  const [focusedChangeIndex, setFocusedChangeIndex] = useState(0)
  const [editingChangeId, setEditingChangeId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

  const entities = useMemo(() => (cs ? groupByEntity(cs.changes) : []), [cs])

  useEffect(() => {
    if (entities.length > 0 && selectedEntityId === null) {
      setSelectedEntityId(entities[0].entityId)
    }
  }, [entities, selectedEntityId])

  const currentEntity = entities.find((e) => e.entityId === selectedEntityId) ?? entities[0]
  const currentChanges = currentEntity?.changes ?? []

  // Keyboard shortcuts: j/k navigate, a/r accept/reject, e edit, Enter
  // apply — guarded against INPUT/TEXTAREA so typing in the editor or
  // find-replace box never triggers a shortcut (docs/PLAN.md §9).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (!cs || currentChanges.length === 0) return

      if (e.key === 'j') {
        setFocusedChangeIndex((i) => Math.min(i + 1, currentChanges.length - 1))
      } else if (e.key === 'k') {
        setFocusedChangeIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'a' || e.key === 'r') {
        const change = currentChanges[focusedChangeIndex]
        if (change) {
          const decision: ChangeDecisionValue = e.key === 'a' ? 'accepted' : 'rejected'
          patchDecisions.mutate([{ change_id: change.id, decision }])
        }
      } else if (e.key === 'A') {
        patchDecisions.mutate(
          currentChanges.map((c) => ({ change_id: c.id, decision: 'accepted' as const })),
        )
      } else if (e.key === 'e') {
        const change = currentChanges[focusedChangeIndex]
        if (change && change.diff.kind !== 'binary') {
          setEditingChangeId(change.id)
          setEditValue(String(change.new_value ?? ''))
        }
      } else if (e.key === 'Enter' && cs.state === 'draft' && !activeJob) {
        runApply()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cs, currentChanges, focusedChangeIndex, changeSetId])

  if (isLoading) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Loading changeset…" />
      </div>
    )
  }

  if (!cs) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Changeset not found" action={<Button onClick={() => navigate('/changes')}>Back to changes</Button>} />
      </div>
    )
  }

  function decide(change: Change, value: ToggleValue) {
    patchDecisions.mutate([{ change_id: change.id, decision: toggleToDecision(value) }])
  }

  function saveEdit(change: Change) {
    let newValue: unknown = editValue
    if (change.diff.kind === 'multi_text') {
      newValue = editValue.split(',').map((v) => v.trim()).filter(Boolean)
    } else if (change.diff.kind === 'scalar' && typeof change.old_value === 'number') {
      const parsed = Number(editValue)
      newValue = Number.isNaN(parsed) ? editValue : parsed
    }
    patchDecisions.mutate([{ change_id: change.id, decision: 'accepted', new_value: newValue }])
    setEditingChangeId(null)
  }

  const allChanges = cs.changes

  function acceptAll() {
    patchDecisions.mutate(allChanges.map((c) => ({ change_id: c.id, decision: 'accepted' as const })))
  }

  function acceptAllNonDestructive() {
    patchDecisions.mutate(
      allChanges
        .filter((c) => c.severity !== 'destructive')
        .map((c) => ({ change_id: c.id, decision: 'accepted' as const })),
    )
  }

  function acceptFieldAcrossTracks(field: string) {
    patchDecisions.mutate(
      allChanges.filter((c) => c.field === field).map((c) => ({ change_id: c.id, decision: 'accepted' as const })),
    )
  }

  const isBulkSingleton = cs.scope_type === 'track' && entities.length > 1 && cs.source === 'manual_edit'

  return (
    <div style={{ display: 'flex', height: '100vh', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)' }}>
      {/* Left pane: entity list (collapses implicitly when there's exactly one entity — singleton mode) */}
      {entities.length > 1 && (
        <aside
          style={{
            width: 240,
            flexShrink: 0,
            borderRight: '1px solid var(--border-subtle)',
            overflowY: 'auto',
            padding: 'var(--space-3)',
          }}
        >
          <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)', padding: 'var(--space-2)' }}>
            {entities.length} {cs.scope_type === 'group' ? 'groups' : 'tracks'}
          </div>
          {entities.map(({ entityId, changes }) => {
            const state = entityChipState(changes)
            return (
              <button
                key={entityId}
                onClick={() => {
                  setSelectedEntityId(entityId)
                  setFocusedChangeIndex(0)
                }}
                style={{
                  display: 'flex',
                  width: '100%',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  padding: '6px 8px',
                  borderRadius: 'var(--radius-md)',
                  border: 'none',
                  background: entityId === selectedEntityId ? 'var(--bg-surface-selected)' : 'transparent',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  fontSize: 'var(--text-sm-size)',
                }}
              >
                <span>#{entityId}</span>
                <Badge tone={CHIP_TONE[state]}>{changes.length}</Badge>
              </button>
            )
          })}
        </aside>
      )}

      {/* Center pane: diff rows */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflowY: 'auto' }}>
        <div style={{ padding: 'var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
              {cs.title}
            </h1>
            <Badge tone="neutral">{cs.state}</Badge>
            <Badge tone="neutral">{cs.source}</Badge>
            {cs.candidate_source && <Badge tone="accent">{cs.candidate_source}</Badge>}
          </div>
          {cs.error && (
            <div style={{ marginTop: 8, color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
              {cs.error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 'var(--space-4)', flexWrap: 'wrap' }}>
            {cs.state === 'draft' && (
              <>
                <Button variant="secondary" size="sm" onClick={acceptAll}>
                  Accept all
                </Button>
                <Button variant="secondary" size="sm" onClick={acceptAllNonDestructive}>
                  Accept all non-destructive
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={applyMutation.isPending || activeJob !== null}
                  onClick={runApply}
                >
                  Apply
                </Button>
              </>
            )}
            {(cs.state === 'applied' || cs.state === 'partially_applied') && (
              <Button
                variant="secondary"
                size="sm"
                disabled={undoMutation.isPending || activeJob !== null}
                onClick={runUndo}
              >
                Undo
              </Button>
            )}
          </div>
          {activeJob && (
            <div style={{ marginTop: 'var(--space-3)', maxWidth: 320 }}>
              <ProgressBar
                value={
                  jobEvents.latestProgress?.total
                    ? Math.round((jobEvents.latestProgress.current / jobEvents.latestProgress.total) * 100)
                    : 0
                }
                label={jobEvents.latestProgress?.message ?? `${activeJob.action === 'apply' ? 'Applying' : 'Undoing'}…`}
              />
            </div>
          )}
          {isBulkSingleton && (
            <div style={{ marginTop: 8, fontSize: 'var(--text-xs-size)', color: 'var(--diff-conflict)' }}>
              Bulk singleton mode — these tracks share nothing; cross-track actions apply to every visible row.
            </div>
          )}
        </div>

        <div style={{ flex: 1 }}>
          {currentChanges.length === 0 ? (
            <div style={{ padding: 'var(--space-9)' }}>
              <EmptyState title="No changes in this entity" />
            </div>
          ) : (
            currentChanges.map((change, idx) => (
              <div
                key={change.id}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 'var(--space-4)',
                  padding: 'var(--space-4) var(--space-5)',
                  borderBottom: '1px solid var(--border-subtle)',
                  background: idx === focusedChangeIndex ? 'var(--bg-surface-hover)' : 'transparent',
                }}
              >
                <div style={{ width: 160, flexShrink: 0 }}>
                  <div style={{ fontSize: 'var(--text-sm-size)', fontWeight: 'var(--font-weight-medium)' }}>
                    {change.diff.label}
                  </div>
                  <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                    {change.severity === 'destructive' && <Badge tone="removed">destructive</Badge>}
                    {change.is_manual && <Badge tone="accent">manual</Badge>}
                    {change.apply_state === 'conflicted' && <Badge tone="conflict">conflict</Badge>}
                  </div>
                  {change.confidence !== null && (
                    <div style={{ marginTop: 6 }}>
                      <ConfidenceBar value={Math.round(change.confidence * 100)} width={80} />
                    </div>
                  )}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  {editingChangeId === change.id ? (
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') saveEdit(change)
                          if (e.key === 'Escape') setEditingChangeId(null)
                        }}
                        style={{
                          flex: 1,
                          padding: '6px 10px',
                          fontFamily: 'var(--font-sans)',
                          fontSize: 'var(--text-sm-size)',
                          border: '1px solid var(--accent-solid)',
                          borderRadius: 'var(--radius-md)',
                          background: 'var(--bg-surface)',
                          color: 'var(--text-primary)',
                        }}
                      />
                      <Button size="sm" onClick={() => saveEdit(change)}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingChangeId(null)}>
                        Cancel
                      </Button>
                    </div>
                  ) : change.diff.kind === 'binary' ? (
                    <div style={{ fontSize: 'var(--text-sm-size)' }}>
                      <span style={{ color: 'var(--diff-removed)' }}>{change.diff.binary?.old_summary ?? 'none'}</span>
                      {' → '}
                      <span style={{ color: 'var(--diff-added)' }}>{change.diff.binary?.new_summary ?? 'none'}</span>
                    </div>
                  ) : change.diff.kind === 'multi_text' ? (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', fontSize: 'var(--text-sm-size)' }}>
                      {change.diff.multi?.removed.map((v) => (
                        <Badge key={`rm-${v}`} tone="removed">
                          − {v}
                        </Badge>
                      ))}
                      {change.diff.multi?.unchanged.map((v) => (
                        <Badge key={`u-${v}`} tone="unchanged">
                          {v}
                        </Badge>
                      ))}
                      {change.diff.multi?.added.map((v) => (
                        <Badge key={`add-${v}`} tone="added">
                          + {v}
                        </Badge>
                      ))}
                    </div>
                  ) : change.diff.kind === 'text' ? (
                    <div style={{ fontSize: 'var(--text-sm-size)', fontFamily: 'var(--font-mono)' }}>
                      <div>
                        <InlineDiff spans={change.diff.old_spans} side="old" />
                      </div>
                      <div style={{ marginTop: 2 }}>
                        <InlineDiff spans={change.diff.new_spans} side="new" />
                      </div>
                    </div>
                  ) : (
                    <div style={{ fontSize: 'var(--text-sm-size)', fontFamily: 'var(--font-mono)' }}>
                      <span style={{ color: 'var(--diff-removed)' }}>{String(change.diff.old_value ?? '—')}</span>
                      {' → '}
                      <span style={{ color: 'var(--diff-added)' }}>{String(change.diff.new_value ?? '—')}</span>
                    </div>
                  )}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  {entities.length > 1 && (
                    <Button variant="ghost" size="sm" onClick={() => acceptFieldAcrossTracks(change.field)}>
                      Accept across all
                    </Button>
                  )}
                  {change.diff.kind !== 'binary' && cs.state === 'draft' && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditingChangeId(change.id)
                        setEditValue(String(change.new_value ?? ''))
                      }}
                    >
                      Edit
                    </Button>
                  )}
                  <ThreeStateToggle
                    value={decisionToToggle(change.decision)}
                    disabled={cs.state !== 'draft'}
                    onChange={(v) => decide(change, v)}
                  />
                </div>
              </div>
            ))
          )}
        </div>
      </main>

      {/* Right pane: candidate picker (docs/PLAN.md §9) — a ranked
          (source, release) row list, never a per-field provenance panel.
          Picking a row re-stages the whole changeset. */}
      <aside
        style={{
          width: 320,
          flexShrink: 0,
          borderLeft: '1px solid var(--border-subtle)',
          padding: 'var(--space-5)',
          overflowY: 'auto',
        }}
      >
        {cs.state === 'draft' ? (
          <CandidatePicker
            scopeType={cs.scope_type}
            scopeId={cs.scope_id}
            currentCandidateSource={cs.candidate_source}
            currentCandidateRef={cs.candidate_ref}
            onStaged={(newChangeSetId) => {
              if (newChangeSetId !== changeSetId) navigate(`/changes/${newChangeSetId}`)
            }}
          />
        ) : (
          <EmptyState
            title="Not editable"
            description={`This changeset is ${cs.state} — candidates can only be re-staged from a draft.`}
          />
        )}
      </aside>
    </div>
  )
}
