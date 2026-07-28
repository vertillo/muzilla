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

/** Consistent page header used across every screen (docs/PLAN.md §12e
 * step 5.3): title, an optional breadcrumb back to a parent list, and
 * page-specific actions only — replaces each page's own improvised
 * "Catalog" / "Jobs" / "Back to X" navigation button. `children`
 * renders below the title row (e.g. ChangeSetReview's badges, a
 * secondary line of metadata) so callers aren't limited to a single
 * title string. */
export function PageHeader({ title, breadcrumb, actions, children }: PageHeaderProps) {
  return (
    <div
      style={{
        padding: 'var(--space-5)',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      {breadcrumb && (
        <div style={{ marginBottom: 6, fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
          <Link to={breadcrumb.to} style={{ color: 'inherit', textDecoration: 'none' }}>
            {breadcrumb.label}
          </Link>
          <span style={{ margin: '0 6px' }}>/</span>
          <span style={{ color: 'var(--text-secondary)' }}>{title}</span>
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          {title}
        </h1>
        {actions && <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>{actions}</div>}
      </div>
      {children}
    </div>
  )
}
