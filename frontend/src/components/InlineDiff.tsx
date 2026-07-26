import type { InlineSpan } from '@/lib/types'

/** Renders a FieldDiff's old_spans/new_spans (docs/PLAN.md §4/§9's
 * char-level inline diff — "Beatles" -> "The Beatles" highlights only
 * the changed head, never the whole string). */
export function InlineDiff({ spans, side }: { spans: InlineSpan[]; side: 'old' | 'new' }) {
  if (spans.length === 0) {
    return <span style={{ color: 'var(--text-muted)' }}>&mdash;</span>
  }
  return (
    <span
      style={{
        textDecoration: side === 'old' ? undefined : undefined,
      }}
    >
      {spans.map((s, i) => {
        if (s.op === 'equal') {
          return <span key={i}>{s.text}</span>
        }
        if (s.op === 'delete') {
          return (
            <span
              key={i}
              style={{
                background: 'var(--diff-removed-bg)',
                color: 'var(--diff-removed)',
                textDecoration: 'line-through',
                borderRadius: 2,
              }}
            >
              {s.text}
            </span>
          )
        }
        return (
          <span
            key={i}
            style={{
              background: 'var(--diff-added-bg)',
              color: 'var(--diff-added)',
              borderRadius: 2,
            }}
          >
            {s.text}
          </span>
        )
      })}
    </span>
  )
}
