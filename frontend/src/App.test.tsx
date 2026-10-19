import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { Link, Outlet } from 'react-router-dom'
import { App } from '@/App'
import { useAuthStore } from '@/store/auth'

const state = vi.hoisted(() => ({ crash: false }))
vi.mock('@/hooks/useAuth', () => ({
  useAuthStatus: () => ({ isLoading: false }),
  useLogin: () => ({
    isPending: false,
    mutate: (_password: string, options: { onSuccess: () => void }) => {
      useAuthStore.getState().setAuthenticated(true, true)
      options.onSuccess()
    },
  }),
}))
const destinations = [
  '/catalog', '/catalog/12', '/reviews', '/reviews/7', '/reviews/7/search',
  '/activity', '/import', '/import/session-1', '/settings', '/dev/components', '/unknown',
]
vi.mock('@/components/AppShell', () => ({
  AppShell: () => <><nav>{destinations.map((to) => <Link key={to} to={to}>{to}</Link>)}</nav><Outlet /></>,
}))
vi.mock('@/pages/Dashboard', () => ({ Dashboard: () => {
  if (state.crash) throw new Error('Unexpected dashboard data')
  return <div>Dashboard destination</div>
} }))
vi.mock('@/pages/Catalog', () => ({ Catalog: () => <div>Catalog destination</div> }))
vi.mock('@/pages/TrackDetail', () => ({ TrackDetail: () => <div>Track destination</div> }))
vi.mock('@/pages/ReviewInbox', () => ({ ReviewInbox: () => <div>Inbox destination</div> }))
vi.mock('@/pages/ReviewDetail', () => ({ ReviewDetail: () => <div>Review destination</div> }))
vi.mock('@/pages/ReviewManualSearch', () => ({ ReviewManualSearch: () => <div>Search destination</div> }))
vi.mock('@/pages/Jobs', () => ({ Jobs: () => <div>Activity destination</div> }))
vi.mock('@/pages/ImportWizard', () => ({ ImportWizard: () => <div>Import destination</div> }))
vi.mock('@/pages/ImportReview', () => ({ ImportReview: () => <div>Import review destination</div> }))
vi.mock('@/pages/Settings', () => ({ Settings: () => <div>Settings destination</div> }))
vi.mock('@/pages/ComponentGallery', () => ({ ComponentGallery: () => <Link to="/">Gallery destination</Link> }))

it('preserves protected login redirects, route nesting, wildcard replacement and the existing render-error fallback', async () => {
  useAuthStore.getState().setAuthenticated(false, true)
  render(<App />)
  await screen.findByRole('button', { name: 'Sign in' })
  expect(window.location.pathname).toBe('/login')
  fireEvent.change(screen.getByPlaceholderText('Password'), { target: { value: 'test' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  await screen.findByText('Dashboard destination')
  expect(window.location.pathname).toBe('/')
  const pages = ['Catalog', 'Track', 'Inbox', 'Review', 'Search', 'Activity', 'Import', 'Import review', 'Settings']
  for (const [index, name] of pages.entries()) {
    fireEvent.click(screen.getByRole('link', { name: destinations[index] }))
    await screen.findByText(`${name} destination`)
    expect(window.location.pathname).toBe(destinations[index])
  }
  fireEvent.click(screen.getByRole('link', { name: '/dev/components' }))
  fireEvent.click(await screen.findByRole('link', { name: 'Gallery destination' }))
  await screen.findByText('Dashboard destination')
  fireEvent.click(screen.getByRole('link', { name: '/unknown' }))
  await screen.findByText('Dashboard destination')
  expect(window.location.pathname).toBe('/')
  // A child render throw must reach Muzilla's reload fallback, not the router's default error UI.
  fireEvent.click(screen.getByRole('link', { name: '/catalog' }))
  await screen.findByText('Catalog destination')
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  try {
    state.crash = true
    fireEvent.click(screen.getByRole('link', { name: '/unknown' }))
    await screen.findByText('Something went wrong')
    expect(screen.getByText('Unexpected dashboard data')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
    expect(screen.queryByText('Unexpected Application Error!')).not.toBeInTheDocument()
  } finally {
    consoleError.mockRestore()
  }
})
