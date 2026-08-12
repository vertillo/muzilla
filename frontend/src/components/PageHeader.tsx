import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export interface Breadcrumb {
  label: string
  to: string
}

export interface PageHeaderProps {
  title: ReactNode
  breadcrumb?: Breadcrumb
  actions?: ReactNode
  children?: ReactNode
}

/** Shared page header with an optional parent breadcrumb, actions, and a
 * secondary content row below the title. */
export function PageHeader({ title, breadcrumb, actions, children }: PageHeaderProps) {
  return (
    <div className="p-5 border-b border-border-subtle">
      {breadcrumb && (
        <div className="mb-[6px] text-xs text-text-muted">
          <Link to={breadcrumb.to} className="text-inherit no-underline">
            {breadcrumb.label}
          </Link>
          <span className="mx-[6px]">/</span>
          <span className="text-text-secondary">{title}</span>
        </div>
      )}
      <div className="flex items-center gap-4">
        <h1 className="text-lg font-semibold m-0">{title}</h1>
        {actions && <div className="ml-auto flex gap-3">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
