import type { InlineSpan } from '@/lib/types'

/** Renders a FieldDiff's old_spans/new_spans (docs/product-spec.md
 * char-level inline diff — "Beatles" -> "The Beatles" highlights only
 * the changed head, never the whole string). */
export function InlineDiff({ spans }: { spans: InlineSpan[]; side: 'old' | 'new' }) {
  if (spans.length === 0) {
    return <span className="text-text-muted">&mdash;</span>
  }
  return (
    <span>
      {spans.map((s, i) => {
        if (s.op === 'equal') {
          return <span key={i}>{s.text}</span>
        }
        if (s.op === 'delete') {
          return (
            <span key={i} className="line-through rounded-[2px] bg-diff-removed-bg text-diff-removed">
              {s.text}
            </span>
          )
        }
        return (
          <span
            key={i}
            // docs/product-spec.md: "never rely on hue alone; always pair
            // with the +/−/▲ iconography" — deletions already pair
            // color with strikethrough; insertions had no secondary
            // signal until this underline.
            className="underline rounded-[2px] bg-diff-added-bg text-diff-added"
          >
            {s.text}
          </span>
        )
      })}
    </span>
  )
}
