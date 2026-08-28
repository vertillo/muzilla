// Client-side fallback for values that have not round-tripped through the
// API. The backend remains authoritative for persisted FieldDiff spans.
type InlineSpan = { op: 'equal' | 'insert' | 'delete'; text: string }

export function diffText(oldValue: string, newValue: string): { oldSpans: InlineSpan[]; newSpans: InlineSpan[] } {
  const a = oldValue ?? ''
  const b = newValue ?? ''

  let prefix = 0
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++

  let suffix = 0
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  ) {
    suffix++
  }

  const oldMid = a.slice(prefix, a.length - suffix)
  const newMid = b.slice(prefix, b.length - suffix)
  const prefixText = a.slice(0, prefix)
  const oldSuffixText = a.slice(a.length - suffix)
  const newSuffixText = b.slice(b.length - suffix)

  const oldSpans: InlineSpan[] = []
  const newSpans: InlineSpan[] = []
  if (prefixText) {
    oldSpans.push({ op: 'equal', text: prefixText })
    newSpans.push({ op: 'equal', text: prefixText })
  }
  if (oldMid) oldSpans.push({ op: 'delete', text: oldMid })
  if (newMid) newSpans.push({ op: 'insert', text: newMid })
  if (oldSuffixText) oldSpans.push({ op: 'equal', text: oldSuffixText })
  if (newSuffixText) newSpans.push({ op: 'equal', text: newSuffixText })

  return { oldSpans, newSpans }
}
