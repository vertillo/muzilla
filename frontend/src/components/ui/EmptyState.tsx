import type { ReactNode } from 'react'

export interface EmptyStateProps {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center text-center gap-[6px] p-9 text-text-secondary">
      <div className="w-9 h-9 rounded-lg border border-dashed border-border-default flex items-center justify-center mb-2 font-mono text-md text-text-muted">
        &#9679;
      </div>
      <div className="font-sans text-md font-medium text-text-primary">{title}</div>
      {description && (
        <div className="font-sans text-sm text-text-muted max-w-[320px]">{description}</div>
      )}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
