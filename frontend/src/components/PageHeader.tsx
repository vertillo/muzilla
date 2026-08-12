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

/** Consistent page header used across every screen (docs/product-spec.md
 * step 5.3): title, an optional breadcrumb back to a parent list, and
 * page-specific actions only — replaces each page's own improvised
 * "Catalog" / "Jobs" / "Back to X" navigation button. `children`
 * renders below the title row (e.g. ChangeSetReview's badges, a
 * secondary line of metadata) so callers aren't limited to a single
 * title string. */
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
