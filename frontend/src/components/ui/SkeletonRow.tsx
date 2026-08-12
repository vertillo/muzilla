export interface SkeletonRowsProps {
  count?: number
}

/** Loading placeholder for list screens (docs/product-spec.md
 * item 5): every list used `EmptyState title="Loading…"`, which
 * collapses the row area to a single centered line and then jumps to
 * full height once data arrives. Row-shaped bars in TableRow's own
 * height keep the layout stable across that transition. */
export function SkeletonRows({ count = 8 }: SkeletonRowsProps) {
  return (
    <div>
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="flex items-center px-3 border-b border-border-subtle h-[var(--row-height-default)]"
        >
          <div
            className="h-4 rounded-sm bg-surface-raised"
            style={{ width: `${55 + ((i * 13) % 30)}%`, animation: 'var(--skeleton-pulse)' }}
          />
        </div>
      ))}
    </div>
  )
}
