import { describe, expect, it } from 'vitest'
import { diffText } from '@/lib/diff'

describe('diffText', () => {
  it('isolates a single-character diacritic change (the §9 example)', () => {
    expect(diffText('Sigur Ros', 'Sigur Rós')).toEqual({
      oldSpans: [
        { op: 'equal', text: 'Sigur R' },
        { op: 'delete', text: 'o' },
        { op: 'equal', text: 's' },
      ],
      newSpans: [
        { op: 'equal', text: 'Sigur R' },
        { op: 'insert', text: 'ó' },
        { op: 'equal', text: 's' },
      ],
    })
  })

  it('treats an empty old value as a pure insert', () => {
    expect(diffText('', 'new text')).toEqual({
      oldSpans: [],
      newSpans: [{ op: 'insert', text: 'new text' }],
    })
  })

  it('treats an empty new value as a pure delete', () => {
    expect(diffText('old text', '')).toEqual({
      oldSpans: [{ op: 'delete', text: 'old text' }],
      newSpans: [],
    })
  })

  it('produces only equal spans for identical strings', () => {
    expect(diffText('same', 'same')).toEqual({
      oldSpans: [{ op: 'equal', text: 'same' }],
      newSpans: [{ op: 'equal', text: 'same' }],
    })
  })

  it('produces a single delete/insert pair for a full replacement with no shared prefix or suffix', () => {
    expect(diffText('abc', 'xyz')).toEqual({
      oldSpans: [{ op: 'delete', text: 'abc' }],
      newSpans: [{ op: 'insert', text: 'xyz' }],
    })
  })

  it('treats null/undefined as empty strings', () => {
    expect(diffText(null as unknown as string, 'x')).toEqual({
      oldSpans: [],
      newSpans: [{ op: 'insert', text: 'x' }],
    })
    expect(diffText('x', undefined as unknown as string)).toEqual({
      oldSpans: [{ op: 'delete', text: 'x' }],
      newSpans: [],
    })
  })
})
