export interface SkeletonRowsProps {
  count?: number
}

/** Loading placeholder for list screens (docs/PLAN.md §12e step 6.5
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
          style={{
            display: 'flex',
            alignItems: 'center',
            height: 'var(--row-height-default)',
            padding: '0 var(--space-3)',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              height: 12,
              width: `${55 + ((i * 13) % 30)}%`,
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-surface-raised)',
              animation: 'var(--skeleton-pulse)',
            }}
          />
        </div>
      ))}
    </div>
  )
}
