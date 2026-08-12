import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Badge, Button, ConfidenceBar, EmptyState, Modal, ProgressBar, ThreeStateToggle, ThumbnailTile, type ToggleValue } from '@/components/ui'
import { InlineDiff } from '@/components/InlineDiff'
import { CandidatePicker } from '@/components/CandidatePicker'
import { PageHeader } from '@/components/PageHeader'
import {
  useApplyChangeset,
  useChangeset,
  usePatchDecisions,
  useUndoChangeset,
} from '@/hooks/useChangesets'
import { useJobEvents } from '@/hooks/useJobEvents'
import { useToasts } from '@/hooks/useToasts'
import { blobUrl, getJob } from '@/lib/api'
import { entityChipState, groupByEntity } from '@/lib/changeset'
import { lyricsText, withEditedLyricsText } from '@/lib/lyrics'
import type { Change, ChangeDecisionValue } from '@/lib/types'

// docs/product-spec.md: ThreeStateToggle.d.ts's accept|pending|reject is
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

  // Apply/undo enqueue a job and return immediately (docs/product-spec.md:
  // `POST .../apply -> 202 {job_id}`) — track which job (if any) is
  // in flight and which action it represents, then subscribe via SSE.
  const [activeJob, setActiveJob] = useState<{ id: number; action: 'apply' | 'undo' } | null>(null)
  const jobEvents = useJobEvents(activeJob?.id ?? null)
  const [confirmAction, setConfirmAction] = useState<'apply' | 'undo' | null>(null)
  const [showShortcuts, setShowShortcuts] = useState(false)

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
        //
        // docs/product-spec.md: "Undo staged" reads as done. Name
        // the next action instead — nothing is reverted on disk until
        // this new draft is itself reviewed and applied.
        toasts.push({ tone: 'info', title: 'Review and apply to finish the undo' })
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

  // docs/product-spec.md: label changeset entities by track/group
  // rather than raw database id. cs.entities is the backend's batched
  // lookup (services/changesets.py::_build_entities); this maps it by
  // id so the per-entity chip list below can look up a label in O(1)
  // and falls back to #{entityId} only when the lookup misses.
  const entityLabelById = useMemo(() => {
    const map = new Map<number, { label: string; sortKey: number | null }>()
    for (const e of cs?.entities ?? []) {
      map.set(e.entity_id, { label: e.label, sortKey: e.sort_key })
    }
    return map
  }, [cs])

  const entities = useMemo(() => {
    if (!cs) return []
    const grouped = groupByEntity(cs.changes)
    return [...grouped].sort((a, b) => {
      const la = entityLabelById.get(a.entityId)
      const lb = entityLabelById.get(b.entityId)
      const aKey = la?.sortKey ?? null
      const bKey = lb?.sortKey ?? null
      if (aKey !== null && bKey !== null && aKey !== bKey) return aKey - bKey
      if (aKey !== null && bKey === null) return -1
      if (aKey === null && bKey !== null) return 1
      const aLabel = la?.label ?? `#${a.entityId}`
      const bLabel = lb?.label ?? `#${b.entityId}`
      return aLabel.localeCompare(bLabel)
    })
  }, [cs, entityLabelById])

  useEffect(() => {
    if (entities.length > 0 && selectedEntityId === null) {
      setSelectedEntityId(entities[0].entityId)
    }
  }, [entities, selectedEntityId])

  const currentEntity = entities.find((e) => e.entityId === selectedEntityId) ?? entities[0]
  const currentChanges = currentEntity?.changes ?? []

  // Keyboard shortcuts: j/k navigate, a/r accept/reject, e edit, Enter
  // apply — guarded against INPUT/TEXTAREA so typing in the editor or
  // find-replace box never triggers a shortcut (docs/product-spec.md).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return

      // docs/product-spec.md: keyboard-first operation only holds
      // if the keys are findable — `?` works even with no changes
      // loaded yet, unlike every other shortcut below.
      if (e.key === '?') {
        setShowShortcuts((v) => !v)
        return
      }
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
          setEditValue(initialEditValue(change))
        }
      } else if (e.key === 'Enter' && cs.state === 'draft' && !activeJob) {
        // docs/product-spec.md: Enter opens the confirmation modal,
        // it never applies directly — a keystroke writing to disk with
        // no prompt was the sharpest edge in the product.
        setConfirmAction('apply')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cs, currentChanges, focusedChangeIndex, changeSetId])

  if (isLoading) {
    return (
      <div className="p-9">
        <EmptyState title="Loading changeset…" />
      </div>
    )
  }

  if (!cs) {
    return (
      <div className="p-9">
        <EmptyState title="Changeset not found" action={<Button onClick={() => navigate('/changes')}>Back to changes</Button>} />
      </div>
    )
  }

  function decide(change: Change, value: ToggleValue) {
    patchDecisions.mutate([{ change_id: change.id, decision: toggleToDecision(value) }])
  }

  function initialEditValue(change: Change): string {
    return change.op === 'write_lyrics' ? lyricsText(change.new_value) : String(change.new_value ?? '')
  }

  function saveEdit(change: Change) {
    let newValue: unknown = editValue
    if (change.op === 'write_lyrics') {
      newValue = withEditedLyricsText(change.new_value, editValue)
    } else if (change.diff.kind === 'multi_text') {
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

  // docs/product-spec.md: the confirmation modal's counts.
  // "Files that will be written" is entities with >=1 accepted change,
  // not entities.length — a track with every change rejected/pending
  // never gets touched by apply.
  const acceptedChanges = allChanges.filter((c) => c.decision === 'accepted')
  const pendingChanges = allChanges.filter((c) => c.decision === 'pending')
  const destructiveAcceptedCount = acceptedChanges.filter((c) => c.severity === 'destructive').length
  const entitiesWithAcceptedChange = new Set(acceptedChanges.map((c) => c.entity_id)).size
  const includesMove = acceptedChanges.some((c) => c.op === 'move')

  return (
    <div className="flex h-full min-h-0 font-sans text-text-primary bg-canvas">
      {/* Left pane: entity list (collapses implicitly when there's exactly one entity — singleton mode) */}
      {entities.length > 1 && (
        <aside className="w-[240px] shrink-0 border-r border-border-subtle overflow-y-auto p-3">
          <div className="text-xs text-text-muted p-1">
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
                className="flex w-full items-center justify-between gap-3 py-[6px] px-3 rounded-md border-none text-text-primary cursor-pointer text-left text-sm"
                style={{ background: entityId === selectedEntityId ? 'var(--bg-surface-selected)' : 'transparent' }}
              >
                <span className="overflow-hidden text-ellipsis whitespace-nowrap">
                  {entityLabelById.get(entityId)?.label ?? `#${entityId}`}
                </span>
                <Badge tone={CHIP_TONE[state]}>{changes.length}</Badge>
              </button>
            )
          })}
        </aside>
      )}

      {/* Center pane: diff rows */}
      <main className="flex-1 flex flex-col min-w-0 overflow-y-auto">
        <PageHeader title={cs.title} breadcrumb={{ label: 'Changes', to: '/changes' }}>
          <div className="mt-[6px] flex items-center gap-4">
            <Badge tone="neutral">{cs.state}</Badge>
            <Badge tone="neutral">{cs.source}</Badge>
            {cs.candidate_source && <Badge tone="accent">{cs.candidate_source}</Badge>}
          </div>
          {cs.error && (
            <div className="mt-3 text-sm text-diff-removed">
              {cs.error}
            </div>
          )}

          {cs.undo_of_id !== null && (
            // docs/product-spec.md: click Undo -> a job stages a
            // new draft -> a toast -> the user is now sitting on a
            // draft that changed nothing on disk, with nothing on
            // screen saying so. This banner is the fix.
            <div className="mt-3 p-3 rounded-md text-sm bg-accent-subtle text-accent-text">
              This reverts changeset #{cs.undo_of_id}. Nothing has been written back yet — review
              and Apply to finish the undo.
            </div>
          )}

          <div className="flex gap-[6px] mt-4 flex-wrap">
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
                  size={cs.undo_of_id !== null ? 'md' : 'sm'}
                  disabled={applyMutation.isPending || activeJob !== null}
                  onClick={() => setConfirmAction('apply')}
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
                onClick={() => setConfirmAction('undo')}
              >
                Undo
              </Button>
            )}
          </div>
          {activeJob && (
            <div className="mt-3 max-w-[320px]">
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
            <div className="mt-3 text-xs text-diff-conflict">
              Bulk singleton mode — these tracks share nothing; cross-track actions apply to every visible row.
            </div>
          )}
        </PageHeader>

        <div className="flex-1">
          {currentChanges.length === 0 ? (
            <div className="p-9">
              <EmptyState title="No changes in this entity" />
            </div>
          ) : (
            currentChanges.map((change, idx) => (
              <div
                key={change.id}
                className="flex items-start gap-4 py-4 px-5 border-b border-border-subtle"
                style={{ background: idx === focusedChangeIndex ? 'var(--bg-surface-hover)' : 'transparent' }}
              >
                <div className="w-[160px] shrink-0">
                  <div className="text-sm font-medium">{change.diff.label}</div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {change.severity === 'destructive' && <Badge tone="removed">destructive</Badge>}
                    {change.is_manual && <Badge tone="accent">manual</Badge>}
                    {change.apply_state === 'conflicted' && <Badge tone="conflict">conflict</Badge>}
                  </div>
                  {change.confidence !== null && (
                    <div className="mt-[6px]">
                      <ConfidenceBar value={Math.round(change.confidence * 100)} width={80} />
                    </div>
                  )}
                </div>

                <div className="flex-1 min-w-0">
                  {editingChangeId === change.id ? (
                    <div className="flex gap-[6px] items-center">
                      {change.op === 'write_lyrics' ? (
                        <textarea
                          autoFocus
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Escape') setEditingChangeId(null)
                          }}
                          className="flex-1 min-h-24 py-[6px] px-[10px] font-sans text-sm rounded-md bg-surface text-text-primary border border-accent"
                        />
                      ) : (
                        <input
                          autoFocus
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveEdit(change)
                            if (e.key === 'Escape') setEditingChangeId(null)
                          }}
                          className="flex-1 py-[6px] px-[10px] font-sans text-sm rounded-md bg-surface text-text-primary border border-accent"
                        />
                      )}
                      <Button size="sm" onClick={() => saveEdit(change)}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditingChangeId(null)}>
                        Cancel
                      </Button>
                    </div>
                  ) : change.diff.kind === 'binary' ? (
                    <div className="flex items-center gap-4">
                      <div className="flex flex-col items-center gap-1">
                        <ThumbnailTile
                          src={
                            change.diff.binary?.old_blob_id
                              ? blobUrl(change.diff.binary.old_blob_id, 'thumb')
                              : undefined
                          }
                        />
                        <span className="text-2xs text-diff-removed">
                          {change.diff.binary?.old_summary ?? 'none'}
                        </span>
                      </div>
                      <span className="text-text-muted">→</span>
                      <div className="flex flex-col items-center gap-1">
                        <ThumbnailTile
                          src={
                            change.diff.binary?.new_blob_id
                              ? blobUrl(change.diff.binary.new_blob_id, 'thumb')
                              : undefined
                          }
                        />
                        <span className="text-2xs text-diff-added">
                          {change.diff.binary?.new_summary ?? 'none'}
                        </span>
                      </div>
                    </div>
                  ) : change.diff.kind === 'multi_text' ? (
                    <div className="flex gap-[6px] flex-wrap text-sm">
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
                    <div className="text-sm font-mono">
                      <div>
                        <InlineDiff spans={change.diff.old_spans} side="old" />
                      </div>
                      <div className="mt-[2px]">
                        <InlineDiff spans={change.diff.new_spans} side="new" />
                      </div>
                    </div>
                  ) : (
                    <div className="text-sm font-mono">
                      <span className="text-diff-removed">{String(change.diff.old_value ?? '—')}</span>
                      {' → '}
                      <span className="text-diff-added">{String(change.diff.new_value ?? '—')}</span>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-3 shrink-0">
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
                        setEditValue(initialEditValue(change))
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

      {/* Right pane: candidate picker (docs/product-spec.md) — a ranked
          (source, release) row list, never a per-field provenance panel.
          Picking a row re-stages the whole changeset. */}
      <aside className="w-[320px] shrink-0 border-l border-border-subtle p-5 overflow-y-auto">
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

      {confirmAction === 'apply' && (
        <Modal
          title="Apply this changeset?"
          onClose={() => setConfirmAction(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmAction(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={applyMutation.isPending}
                onClick={() => {
                  setConfirmAction(null)
                  runApply()
                }}
              >
                Apply
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-[6px]">
            <div>{acceptedChanges.length} accepted change(s) will be written.</div>
            {pendingChanges.length > 0 && (
              <div>{pendingChanges.length} change(s) are still pending and will be skipped.</div>
            )}
            {destructiveAcceptedCount > 0 && (
              <div className="text-diff-removed">
                {destructiveAcceptedCount} of those are destructive.
              </div>
            )}
            <div>
              {entitiesWithAcceptedChange} file(s) will be written
              {includesMove ? ', including a rename (file move).' : '.'}
            </div>
          </div>
        </Modal>
      )}

      {confirmAction === 'undo' && (
        <Modal
          title="Undo this changeset?"
          onClose={() => setConfirmAction(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmAction(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={undoMutation.isPending}
                onClick={() => {
                  setConfirmAction(null)
                  runUndo()
                }}
              >
                Undo
              </Button>
            </>
          }
        >
          <div>
            This stages a new changeset that reverts changeset #{changeSetId}. Nothing is written
            back to disk until you review and apply that undo changeset.
          </div>
        </Modal>
      )}

      {/* docs/product-spec.md: persistent footer hint so the
          shortcuts are discoverable without needing to already know
          `?` opens the overlay. */}
      <div className="sticky bottom-0 z-10 flex flex-wrap justify-center gap-4 border-t border-border-subtle bg-surface-raised px-4 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] font-mono text-2xs text-text-muted">
        <span>j/k navigate</span>
        <span>a/r accept/reject</span>
        <span>e edit</span>
        <span>Enter apply</span>
        <button
          onClick={() => setShowShortcuts(true)}
          className="bg-transparent border-none cursor-pointer font-[inherit] text-[inherit] p-0 text-accent-text"
        >
          ? for all shortcuts
        </button>
      </div>

      {showShortcuts && (
        // docs/product-spec.md: j/k/a/r/e/A/Enter were implemented
        // and documented nowhere in the UI — keyboard-first operation
        // only holds if the keys are findable.
        <Modal title="Keyboard shortcuts" onClose={() => setShowShortcuts(false)}>
          <div className="flex flex-col gap-3">
            {[
              ['j / k', 'Move focus to the next / previous change'],
              ['a', 'Accept the focused change'],
              ['r', 'Reject the focused change'],
              ['A', 'Accept every change on the current entity'],
              ['e', 'Edit the focused change’s value'],
              ['Enter', 'Open the apply confirmation (draft only)'],
              ['?', 'Toggle this overlay'],
            ].map(([key, description]) => (
              <div key={key} className="flex gap-4 items-baseline">
                <code className="min-w-[56px] py-[2px] px-[6px] rounded-sm bg-surface-raised font-mono text-xs text-center">
                  {key}
                </code>
                <span>{description}</span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  )
}
