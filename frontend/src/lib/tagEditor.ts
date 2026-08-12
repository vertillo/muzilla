// Keep the multiple-values sentinel independent of the editor so bulk-edit
// preservation can be tested without rendering the full tree.
import type { TrackDetail } from '@/lib/types'

export const MULTIPLE_VALUES = Symbol('multiple-values')
export type FieldValue = string | number | boolean | string[] | null | typeof MULTIPLE_VALUES

export function commonValue(tracks: TrackDetail[], field: string): FieldValue {
  if (tracks.length === 0) return null
  const values = tracks.map((t) => (t as unknown as Record<string, unknown>)[field])
  const first = values[0]
  const allSame = values.every((v) => JSON.stringify(v) === JSON.stringify(first))
  if (!allSame) return MULTIPLE_VALUES
  if (Array.isArray(first)) return first as string[]
  if (typeof first === 'string' || typeof first === 'number' || typeof first === 'boolean') return first
  return first === null || first === undefined ? null : (first as FieldValue)
}
