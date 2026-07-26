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
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-4)',
        height: 'var(--row-height-default)',
        padding: '0 var(--space-3)',
        borderBottom: '1px solid var(--border-subtle)',
        borderLeft: `2px solid ${leftBorder}`,
        background: BACKGROUNDS[effective],
        fontFamily: 'var(--font-sans)',
        fontSize: 'var(--text-sm-size)',
        color: 'var(--text-primary)',
        transition: 'background var(--transition-fast)',
      }}
    >
      {children}
    </div>
  )
}
