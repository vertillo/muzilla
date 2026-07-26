import type { ReactNode } from 'react'

export interface EmptyStateProps {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        textAlign: 'center',
        gap: 6,
        padding: 'var(--space-9)',
        color: 'var(--text-secondary)',
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: 'var(--radius-lg)',
          border: '1px dashed var(--border-default)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 4,
          fontFamily: 'var(--font-mono)',
          fontSize: 16,
          color: 'var(--text-muted)',
        }}
      >
        &#9679;
      </div>
      <div
        style={{
          fontFamily: 'var(--font-sans)',
          fontSize: 'var(--text-md-size)',
          fontWeight: 'var(--font-weight-medium)',
          color: 'var(--text-primary)',
        }}
      >
        {title}
      </div>
      {description && (
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 'var(--text-sm-size)',
            color: 'var(--text-muted)',
            maxWidth: 320,
          }}
        >
          {description}
        </div>
      )}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  )
}
