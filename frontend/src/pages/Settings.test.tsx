import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Settings } from '@/pages/Settings'
import { ToastProvider } from '@/hooks/useToasts'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>{children}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

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
})
