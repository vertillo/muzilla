import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useLogout } from '@/hooks/useAuth'
import { useAuthStore } from '@/store/auth'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard' },
  { to: '/catalog', label: 'Catalogo' },
  { to: '/reviews', label: 'Revisioni' },
  { to: '/activity', label: 'Attività' },
  { to: '/settings', label: 'Impostazioni' },
]

interface NavigationProps {
  onNavigate?: () => void
}

function Navigation({ onNavigate }: NavigationProps) {
  const authEnabled = useAuthStore((state) => state.authEnabled)
  const logout = useLogout()

  return (
    <>
      <div className="shrink-0 px-5 pt-5 pb-4 text-lg font-semibold">muzilla</div>
      <nav aria-label="Navigazione principale" className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <div className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              onClick={onNavigate}
              className={({ isActive }) =>
                `focus-ring block min-h-11 rounded-md px-3 py-3 text-sm no-underline ${
                  isActive ? 'bg-surface-selected text-text-primary' : 'text-text-secondary'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      </nav>
      {authEnabled && (
        <div className="shrink-0 border-t border-border-subtle p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <button
            type="button"
            onClick={() => logout.mutate()}
            className="focus-ring min-h-11 w-full rounded-md border-0 bg-transparent px-3 text-left text-sm text-text-secondary"
          >
            Esci
          </button>
        </div>
      )}
    </>
  )
}

export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <div className="flex h-dvh overflow-hidden bg-canvas font-sans text-text-primary">
      <a href="#app-content" className="skip-link">Vai al contenuto</a>

      <aside className="hidden h-full w-52 shrink-0 flex-col border-r border-border-subtle md:flex">
        <Navigation />
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 flex md:hidden" role="presentation">
          <aside id="mobile-navigation" className="relative z-10 flex h-full w-[min(19rem,88vw)] flex-col bg-canvas shadow-md">
            <Navigation onNavigate={() => setDrawerOpen(false)} />
          </aside>
          <button
            type="button"
            aria-label="Chiudi navigazione"
            className="min-w-0 flex-1 border-0 bg-black/60"
            onClick={() => setDrawerOpen(false)}
          />
        </div>
      )}

      <div className="flex min-w-0 min-h-0 flex-1 flex-col">
        <header className="flex min-h-14 shrink-0 items-center border-b border-border-subtle px-3 md:hidden">
          <button
            type="button"
            className="focus-ring min-h-11 rounded-md border-0 bg-transparent px-3 text-sm text-text-primary"
            aria-expanded={drawerOpen}
            aria-controls="mobile-navigation"
            onClick={() => setDrawerOpen(true)}
          >
            Menu
          </button>
          <span className="ml-2 font-semibold">muzilla</span>
        </header>
        <main id="app-content" className="min-h-0 min-w-0 flex-1 overflow-auto" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
