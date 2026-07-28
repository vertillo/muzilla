import { describe, expect, it } from 'vitest'
import { entityChipState, groupByEntity } from '@/lib/changeset'
import type { Change, ChangeApplyState, ChangeDecisionValue } from '@/lib/types'

function change(overrides: Partial<Change>): Change {
  return {
    id: 1,
    seq: 0,
    entity_type: 'track',
    entity_id: 1,
    field: 'title',
    op: 'set',
    old_value: 'a',
    new_value: 'b',
    confidence: null,
    severity: 'normal',
    decision: 'pending',
    apply_state: 'pending',
    is_manual: true,
    diff: {
      field: 'title',
      label: 'Title',
      kind: 'text',
      old_value: 'a',
      new_value: 'b',
      severity: 'normal',
      old_spans: [],
      new_spans: [],
      multi: null,
      binary: null,
    },
    ...overrides,
  }
}

function withDecision(decision: ChangeDecisionValue, apply_state: ChangeApplyState = 'pending') {
  return change({ decision, apply_state })
}

describe('groupByEntity', () => {
  it('groups changes by entity_id, preserving first-seen order', () => {
    const changes = [
      change({ id: 1, entity_id: 2 }),
      change({ id: 2, entity_id: 1 }),
      change({ id: 3, entity_id: 2 }),
    ]
    const groups = groupByEntity(changes)
    expect(groups.map((g) => g.entityId)).toEqual([2, 1])
    expect(groups[0].changes.map((c) => c.id)).toEqual([1, 3])
    expect(groups[1].changes.map((c) => c.id)).toEqual([2])
  })

  it('returns an empty list for no changes', () => {
    expect(groupByEntity([])).toEqual([])
  })

  it('puts a single change into its own single-entry group', () => {
    const groups = groupByEntity([change({ entity_id: 5 })])
    expect(groups).toEqual([{ entityId: 5, changes: [expect.objectContaining({ entity_id: 5 })] }])
  })
})

describe('entityChipState', () => {
  it('is "accepted" when every change is accepted', () => {
    expect(entityChipState([withDecision('accepted'), withDecision('accepted')])).toBe('accepted')
  })

  it('is "rejected" when every change is rejected', () => {
    expect(entityChipState([withDecision('rejected'), withDecision('rejected')])).toBe('rejected')
  })

  it('is "pending" when every change is pending', () => {
    expect(entityChipState([withDecision('pending')])).toBe('pending')
  })

  it('is "mixed" for accepted + rejected', () => {
    expect(entityChipState([withDecision('accepted'), withDecision('rejected')])).toBe('mixed')
  })

  it('is "mixed" for accepted + pending', () => {
    expect(entityChipState([withDecision('accepted'), withDecision('pending')])).toBe('mixed')
  })

  it('is "mixed" for rejected + pending', () => {
    expect(entityChipState([withDecision('rejected'), withDecision('pending')])).toBe('mixed')
  })

  it('is "conflict" whenever any change is conflicted, overriding an otherwise-uniform decision', () => {
    expect(entityChipState([withDecision('accepted', 'conflicted')])).toBe('conflict')
  })

  it('is "conflict" even amid a mix of decisions (conflict has top precedence)', () => {
    expect(
      entityChipState([withDecision('accepted', 'pending'), withDecision('rejected', 'conflicted')]),
    ).toBe('conflict')
  })

  it('treats an empty change list as "mixed" (falls through every branch)', () => {
    // decisions.size === 0, so the `size === 1` branch is skipped entirely
    // and this falls all the way to the final `return 'mixed'` — not a
    // real UI state (no entity has zero changes) but characterizing it
    // pins the function's actual behavior at this boundary.
    expect(entityChipState([])).toBe('mixed')
  })
})
