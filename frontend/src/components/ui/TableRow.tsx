import { useState, type ReactNode } from 'react'

export type TableRowState = 'default' | 'hover' | 'selected' | 'conflicted'

export interface TableRowProps {
  state?: TableRowState
  children?: ReactNode
}

const BACKGROUNDS: Record<TableRowState, string> = {
  default: 'transparent',
  hover: 'var(--bg-surface-hover)',
  selected: 'var(--bg-surface-selected)',
  conflicted: 'var(--diff-conflict-bg)',
}

export function TableRow({ state = 'default', children }: TableRowProps) {
  const [hovering, setHovering] = useState(false)
  const effective: TableRowState = state === 'default' && hovering ? 'hover' : state

  const leftBorder =
    effective === 'selected'
      ? 'var(--accent-solid)'
      : effective === 'conflicted'
        ? 'var(--diff-conflict)'
        : 'transparent'

  return (
    <div
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className="flex items-center gap-4 px-3 border-b border-border-subtle font-sans text-sm text-text-primary h-[var(--row-height-default)]"
      style={{
        borderLeft: `2px solid ${leftBorder}`,
        background: BACKGROUNDS[effective],
        transition: 'background var(--transition-fast)',
      }}
    >
      {children}
    </div>
  )
}
