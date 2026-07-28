import { NavLink, Outlet } from 'react-router-dom'
import { useLogout } from '@/hooks/useAuth'
import { useAuthStore } from '@/store/auth'

const NAV_ITEMS: { to: string; label: string }[] = [
  { to: '/catalog', label: 'Catalog' },
  { to: '/groups', label: 'Groups' },
  { to: '/changes', label: 'Changes' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/duplicates', label: 'Duplicates' },
  { to: '/import', label: 'Import' },
]

export function AppShell() {
  const authEnabled = useAuthStore((s) => s.authEnabled)
  const logout = useLogout()

  return (
    <div style={{ display: 'flex', height: '100vh', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)' }}>
      <aside
        style={{
          width: 200,
          flexShrink: 0,
          borderRight: '1px solid var(--border-subtle)',
          padding: 'var(--space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-6)',
        }}
      >
        <div style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)' }}>
          muzilla
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              style={({ isActive }) => ({
                display: 'block',
                padding: 'var(--space-2) var(--space-3)',
                borderRadius: 'var(--radius-md)',
                fontSize: 'var(--text-sm-size)',
                color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                background: isActive ? 'var(--bg-surface-selected)' : 'transparent',
                textDecoration: 'none',
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {authEnabled && (
          <button
            onClick={() => logout.mutate()}
            style={{
              marginTop: 'auto',
              background: 'none',
              border: 'none',
              color: 'var(--text-muted)',
              fontSize: 'var(--text-xs-size)',
              cursor: 'pointer',
              textAlign: 'left',
              padding: 0,
            }}
          >
            Sign out
          </button>
        )}
      </aside>

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'auto' }}>
        <Outlet />
      </main>
    </div>
  )
}
