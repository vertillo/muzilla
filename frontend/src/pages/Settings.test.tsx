import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Settings } from '@/pages/Settings'
import { ToastProvider } from '@/hooks/useToasts'

function createWrapper(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ToastProvider>
          <MemoryRouter initialEntries={['/settings']}>
            <Routes>
              <Route path="/settings" element={children} />
              <Route path="/" element={<div>Dashboard</div>} />
              <Route path="/login" element={<div>Login</div>} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>
    )
  }
}

function wrapper({ children }: { children: ReactNode }) {
  return createWrapper()({ children })
}

const RESET_OWNED_QUERY_KEYS = [
  ['dashboard-summary'],
  ['changesets', 'all'],
  ['jobs', 'all', null, 100, false],
  ['tracks', ''],
  ['track', 1],
  ['track-facets', ''],
  ['duplicates', false],
  ['job', 1],
] as const

const FIELDS_RESPONSE = {
  items: [
    { name: 'title', label: 'Title', type: 'text', category: 'identity', editable: true, multi_valued: false, default_strip: false },
    { name: 'comment', label: 'Comment', type: 'text', category: 'admin', editable: true, multi_valued: false, default_strip: true },
  ],
}

const SETTINGS_RESPONSE = {
  providers: [
    { provider: 'musicbrainz', enabled: true, token_configured: false },
    { provider: 'discogs', enabled: false, token_configured: false },
  ],
  templates: { album: null, singleton: null, default: null },
  strip_fields: ['comment'],
}

const OLD_SETTINGS_RESPONSE = {
  ...SETTINGS_RESPONSE,
  templates: { album: '$artist/old-override', singleton: null, default: null },
}

const PROVIDER_STATUS_RESPONSE = {
  items: [
    { provider: 'musicbrainz', enabled: true, requires_auth: false, token_configured: true, live: true, state: 'operational', last_success_at: null, last_error_at: null, last_error_detail: null, last_checked_at: '2026-01-01T00:00:00Z', rate_limited: false },
    { provider: 'discogs', enabled: false, requires_auth: true, token_configured: false, live: false, state: 'disabled', last_success_at: null, last_error_at: null, last_error_detail: null, last_checked_at: null, rate_limited: false },
  ],
}

function mockFetchByUrl(routes: Record<string, { method?: string; body: unknown }[]>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = (init?.method ?? 'GET').toUpperCase()
      // Longest-prefix match: '/api/settings' is itself a valid prefix
      // of '/api/settings/templates/preview', so a naive first-match
      // over Object.keys (iteration order not meaningfully "most
      // specific first") can route a POST preview call to the GET
      // settings mock instead.
      const key = Object.keys(routes)
        .filter((k) => url.startsWith(k))
        .sort((a, b) => b.length - a.length)[0]
      if (!key && url.startsWith('/api/providers/status')) {
        return Promise.resolve({ ok: true, json: async () => PROVIDER_STATUS_RESPONSE })
      }
      if (!key) throw new Error(`unmocked fetch: ${method} ${url}`)
      const candidates = routes[key]
      const match = candidates.find((c) => (c.method ?? 'GET') === method) ?? candidates[0]
      return Promise.resolve({ ok: true, json: async () => match.body })
    }),
  )
}

describe('Settings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders provider, template, and strip-rule sections once loaded', async () => {
    mockFetchByUrl({
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
    })

    render(<Settings />, { wrapper })

    await waitFor(() => expect(screen.getByText('MusicBrainz')).toBeInTheDocument())
    expect(screen.getByText('Discogs')).toBeInTheDocument()
    expect(screen.getByText('Album tracks')).toBeInTheDocument()
    expect(screen.getByText('Comment')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Test connection' })).toHaveLength(2)
  })

  it('shows an error state instead of a blank page when settings fail to load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) }),
    )

    render(<Settings />, { wrapper })

    await waitFor(() => expect(screen.getByText("Couldn't load settings")).toBeInTheDocument())
  })

  it('previewing a template shows the rendered sample path', async () => {
    mockFetchByUrl({
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
      '/api/settings/templates/preview': [
        { method: 'POST', body: { path: 'Sigur Rós - Svefn-g-englar', errors: [] } },
      ],
    })

    const user = userEvent.setup()
    render(<Settings />, { wrapper })

    await waitFor(() => expect(screen.getByText('Album tracks')).toBeInTheDocument())
    const previewButtons = screen.getAllByRole('button', { name: 'Preview' })
    await user.click(previewButtons[0])

    await waitFor(() => expect(screen.getByText('Sigur Rós - Svefn-g-englar')).toBeInTheDocument())
  })

  it('a template preview error renders the structural error, not a crash', async () => {
    mockFetchByUrl({
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
      '/api/settings/templates/preview': [
        { method: 'POST', body: { path: '', errors: ['unknown function %bogus'] } },
      ],
    })

    const user = userEvent.setup()
    render(<Settings />, { wrapper })

    await waitFor(() => expect(screen.getByText('Album tracks')).toBeInTheDocument())
    const previewButtons = screen.getAllByRole('button', { name: 'Preview' })
    await user.click(previewButtons[0])

    await waitFor(() => expect(screen.getByText('unknown function %bogus')).toBeInTheDocument())
  })

  it('never renders a provider token value anywhere on the page', async () => {
    mockFetchByUrl({
      '/api/settings': [
        {
          body: {
            providers: [{ provider: 'discogs', enabled: true, token_configured: true }],
            templates: { album: null, singleton: null, default: null },
            strip_fields: [],
          },
        },
      ],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
    })

    render(<Settings />, { wrapper })

    await waitFor(() => expect(screen.getByText('Discogs')).toBeInTheDocument())
    expect(screen.getByText('token set')).toBeInTheDocument()
    // token_configured only ever carries a boolean over the wire (see
    // services/settings.py's docstring) — this asserts the field
    // actually rendered by the page is empty (write-only), not merely
    // that no specific string happens to appear in the DOM.
    const tokenInput = screen.getByPlaceholderText(/Token configured/)
    expect(tokenInput).toHaveValue('')
  })

  it('clears a configured credential only after confirmation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.startsWith('/api/settings/providers/discogs')) {
        return Promise.resolve({ ok: true, json: async () => ({ provider: 'discogs', enabled: true, token_configured: false }) })
      }
      if (url.startsWith('/api/providers/status')) {
        return Promise.resolve({ ok: true, json: async () => PROVIDER_STATUS_RESPONSE })
      }
      if (url.startsWith('/api/settings')) {
        return Promise.resolve({ ok: true, json: async () => ({ ...SETTINGS_RESPONSE, providers: [{ provider: 'discogs', enabled: true, token_configured: true }] }) })
      }
      if (url.startsWith('/api/fields')) {
        return Promise.resolve({ ok: true, json: async () => FIELDS_RESPONSE })
      }
      throw new Error(`unmocked fetch: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Settings />, { wrapper })

    await user.click(await screen.findByRole('button', { name: 'Clear credential' }))
    expect(screen.getByRole('dialog', { name: /Clear Discogs credential/ })).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/settings/providers/discogs',
      expect.objectContaining({ method: 'PUT' }),
    )

    await user.click(screen.getAllByRole('button', { name: 'Clear credential' })[1])
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/settings/providers/discogs',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ token: '' }) }),
    ))
  })

  it('requires the exact catalog scope phrase before reset', async () => {
    mockFetchByUrl({
      '/api/settings/reset/catalog': [
        {
          method: 'POST',
          body: {
            operation_id: 1,
            scope: 'catalog_and_activity',
            state: 'succeeded',
            settings_preserved: true,
            secrets_preserved: true,
            music_files_touched: false,
            deleted_counts: {},
          },
        },
      ],
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
    })
    const user = userEvent.setup()
    render(<Settings />, { wrapper })

    await user.click(await screen.findByRole('button', { name: 'Reset catalog' }))
    const dialog = screen.getByRole('dialog', { name: 'Reset catalog and activity?' })
    expect(dialog).toHaveTextContent('Preserves all Settings and provider credentials')
    expect(dialog).toHaveTextContent('Music files and backups')
    const confirm = screen.getByRole('button', { name: 'Confirm reset' })
    expect(confirm).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox'), 'RESET CATALOG AND ACTIVITY')
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/settings/reset/catalog',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          scope: 'catalog_and_activity',
          confirmation: 'RESET CATALOG AND ACTIVITY',
        }),
      }),
    ))
  })

  it('factory reset additionally requires the current password and explicit scope', async () => {
    mockFetchByUrl({
      '/api/settings/reset/factory': [
        {
          method: 'POST',
          body: {
            operation_id: 2,
            scope: 'factory',
            state: 'succeeded',
            settings_preserved: false,
            secrets_preserved: false,
            music_files_touched: false,
            deleted_counts: {},
          },
        },
      ],
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
    })
    const user = userEvent.setup()
    render(<Settings />, { wrapper })

    await user.click(await screen.findByRole('button', { name: 'Factory reset' }))
    const dialog = screen.getByRole('dialog', { name: 'Factory reset Muzilla?' })
    expect(dialog).toHaveTextContent('FACTORY RESET MUZILLA')
    const confirm = screen.getByRole('button', { name: 'Confirm reset' })
    await user.type(within(dialog).getByRole('textbox'), 'FACTORY RESET MUZILLA')
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText('Current password'), 'hunter2')
    expect(confirm).toBeEnabled()
  })

  it.each([
    {
      action: 'Reset catalog',
      dialog: 'Reset catalog and activity?',
      confirmation: 'RESET CATALOG AND ACTIVITY',
      endpoint: '/api/settings/reset/catalog',
      response: { operation_id: 3, scope: 'catalog_and_activity', state: 'succeeded', settings_preserved: true, secrets_preserved: true, music_files_touched: false, deleted_counts: {} },
    },
    {
      action: 'Factory reset',
      dialog: 'Factory reset Muzilla?',
      confirmation: 'FACTORY RESET MUZILLA',
      endpoint: '/api/settings/reset/factory',
      response: { operation_id: 4, scope: 'factory', state: 'succeeded', settings_preserved: false, secrets_preserved: false, music_files_touched: false, deleted_counts: {} },
    },
  ])('evicts Dashboard, Catalog, Activity, and Settings cache after $action succeeds', async ({ action, dialog, confirmation, endpoint, response }) => {
    mockFetchByUrl({
      [endpoint]: [{ method: 'POST', body: response }],
      '/api/settings': [{ body: SETTINGS_RESPONSE }],
      '/api/fields': [{ body: FIELDS_RESPONSE }],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
    for (const key of RESET_OWNED_QUERY_KEYS) client.setQueryData(key, { stale: true })
    client.setQueryData(['settings'], OLD_SETTINGS_RESPONSE)
    client.setQueryData(['provider-status'], PROVIDER_STATUS_RESPONSE)
    const user = userEvent.setup()
    render(<Settings />, { wrapper: createWrapper(client) })

    await user.click(await screen.findByRole('button', { name: action }))
    await waitFor(() => expect(client.isFetching()).toBe(0))
    const resetDialog = screen.getByRole('dialog', { name: dialog })
    await user.type(within(resetDialog).getByRole('textbox'), confirmation)
    if (action === 'Factory reset') await user.type(screen.getByLabelText('Current password'), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Confirm reset' }))

    await waitFor(() => {
      for (const key of RESET_OWNED_QUERY_KEYS) expect(client.getQueryData(key)).toBeUndefined()
      expect(client.getQueryData(['settings'])).toEqual(SETTINGS_RESPONSE)
    })
  })
})
