// Extracted from ChangeSetReview.tsx (docs/PLAN.md §12e step 4.2) so the
// entity grouping and per-entity decision-state precedence — ported from
// the Change Review.dc.html prototype's groupByEntity()/trackChip() — can
// be characterization-tested independently of the review screen's render
// tree.
import type { Change } from '@/lib/types'

/** Groups changes by entity_id, preserving first-seen order — the left
 * pane's per-entity rollup (docs/PLAN.md §9: "entity list with per-
 * track change counts"). */
export function groupByEntity(changes: Change[]): { entityId: number; changes: Change[] }[] {
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

export function entityChipState(changes: Change[]): 'conflict' | 'rejected' | 'accepted' | 'mixed' | 'pending' {
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
