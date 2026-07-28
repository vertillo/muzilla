import { NavLink, Outlet } from 'react-router-dom'
import { useLogout } from '@/hooks/useAuth'
import { useAuthStore } from '@/store/auth'

const NAV_ITEMS: { to: string; label: string }[] = [
  { to: '/', label: 'Dashboard' },
  { to: '/catalog', label: 'Catalog' },
  { to: '/groups', label: 'Groups' },
  { to: '/changes', label: 'Changes' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/duplicates', label: 'Duplicates' },
  { to: '/import', label: 'Import' },
  { to: '/settings', label: 'Settings' },
]

export function AppShell() {
  const authEnabled = useAuthStore((s) => s.authEnabled)
  const logout = useLogout()

  return (
    <div className="flex h-screen font-sans text-text-primary bg-canvas">
      <aside className="w-[200px] shrink-0 border-r border-border-subtle p-5 flex flex-col gap-6">
        <div className="text-lg font-semibold">muzilla</div>

        <nav className="flex flex-col gap-[2px]">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `block py-2 px-3 rounded-md text-sm no-underline ${
                  isActive ? 'text-text-primary bg-surface-selected' : 'text-text-secondary bg-transparent'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {authEnabled && (
          <button
            onClick={() => logout.mutate()}
            className="mt-auto bg-transparent border-none text-text-muted text-xs cursor-pointer text-left p-0"
          >
            Sign out
          </button>
        )}
      </aside>

      <main className="flex-1 flex flex-col min-w-0 min-h-0">
        <Outlet />
      </main>
    </div>
  )
}
