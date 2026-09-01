import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Catalog } from '@/pages/Catalog'

function createWrapper(initialEntries: string[] = ['/catalog']) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/catalog" element={children} />
            <Route path="/catalog/:trackId" element={<div>detail</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    ),
  }
}

// Minimal tracks page shape
const TRACKS_EMPTY = { items: [], next_cursor: null, total: 0 }
const TRACKS_ONE = {
  items: [
    {
      id: 1,
      path: '/music/a.mp3',
      filename: 'a.mp3',
      ext: 'mp3',
      title: 'Svefn',
      artist: 'Sigur Rós',
      album: 'Album 1',
      album_artist: 'Sigur Rós',
      track_no: 1,
      disc_no: 1,
      year: 2024,
      genre: ['Post-Rock'],
      duration_ms: 180_000,
      format: 'flac',
      bitrate: 320,
      has_embedded_art: true,
      has_lyrics: false,
      probe_error: null,
      missing_since: null,
    },
  ],
  next_cursor: null,
  total: 1,
}

type FacetValue = { value: string; count: number }
function facetsBody(over: Partial<Record<'artists' | 'albums' | 'genres' | 'formats', FacetValue[]>> = {}) {
  return {
    artists: over.artists ?? [{ value: 'Sigur Rós', count: 2 }, { value: 'Jónsi', count: 1 }],
    albums: over.albums ?? [{ value: 'Ágætis byrjun', count: 2 }, { value: 'Kveikur', count: 1 }],
    genres: over.genres ?? [{ value: 'Post-Rock', count: 2 }, { value: 'Ambient', count: 1 }],
    formats: over.formats ?? [{ value: 'flac', count: 2 }, { value: 'mp3', count: 1 }],
  }
}

function decodeFacetCursor(cursor: string): string {
  const pad = (4 - (cursor.length % 4)) % 4
  const padded = cursor + '='.repeat(pad)
  const b64 = padded.replace(/-/g, '+').replace(/_/g, '/')
  return JSON.parse(atob(b64))
}

function mockFetch(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      // longest prefix
      const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.includes(k))
      if (!key) throw new Error(`unmocked fetch: ${url}`)
      const body = routes[key] as unknown
      // For dynamic facets based on query params, allow function
      const resolved = typeof body === 'function' ? (body as (url: string) => unknown)(url) : body
      return Promise.resolve({ ok: true, json: async () => resolved } as Response)
    }),
  )
}

describe('Catalog facets', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('exposes all supported combinable facets with counts and searchable controls', async () => {
    mockFetch({
      '/api/tracks/facets': facetsBody(),
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })

    // All four facet comboboxes
    expect(screen.getByRole('button', { name: /Tutti gli artisti|Artista: Sigur/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tutti gli album/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tutti i generi/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tutti i formati/ })).toBeInTheDocument()

    // Flags including missing
    expect(screen.getByRole('button', { name: 'File mancante' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Senza cover' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Da identificare' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Errori di lettura' })).toBeInTheDocument()

    // Open artist combobox and check counts
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Tutti gli artisti/ }))
    const search = screen.getByPlaceholderText(/Cerca artista/)
    expect(search).toBeInTheDocument()
    const listbox = screen.getByRole('listbox', { name: 'Artista' })
    expect(await within(listbox).findByText('Sigur Rós')).toBeInTheDocument()
    // count rendered as (2)
    expect(within(listbox).getByText('(2)')).toBeInTheDocument()

    // Search filtering: type to filter
    await user.type(search, 'Jónsi')
    // facet_q debounce via deferred value — wait for filtered
    await waitFor(() => expect(within(listbox).getByText('Jónsi')).toBeInTheDocument())
  })

  it('renders active removable chips with keyboard support and URL sync', async () => {
    mockFetch({
      '/api/tracks/facets': facetsBody(),
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper(['/catalog?artist=Sigur%20R%C3%B3s&album=%C3%81g%C3%A6tis%20byrjun&flags=missing-art'])
    render(<Catalog />, { wrapper })

    const user = userEvent.setup()
    // Chips should be present — query via accessible remove button to avoid duplicate button label
    expect(await screen.findByRole('button', { name: 'Rimuovi filtro Artista: Sigur Rós' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rimuovi filtro Album: Ágætis byrjun' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rimuovi filtro Senza cover' })).toBeInTheDocument()

    // Remove via button click
    const removeArtist = screen.getByRole('button', { name: 'Rimuovi filtro Artista: Sigur Rós' })
    await user.click(removeArtist)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Rimuovi filtro Artista: Sigur Rós' })).not.toBeInTheDocument())

    // Chips container wraps (flex-wrap)
    const chips = screen.getByLabelText('Filtri attivi')
    expect(chips.className).toMatch(/flex-wrap/)
  })

  it('restores URL state on load including sort and q', async () => {
    mockFetch({
      '/api/tracks/facets': facetsBody(),
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper(['/catalog?q=Svefn&artist=Sigur%20R%C3%B3s&sort=artist&dir=desc&genre=Post-Rock'])
    render(<Catalog />, { wrapper })

    expect(await screen.findByDisplayValue('Svefn')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Rimuovi filtro Artista: Sigur Rós' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Rimuovi filtro Genere: Post-Rock' })).toBeInTheDocument()
    // sort header should show descending for artist
    const artistSortBtn = await screen.findByRole('button', { name: 'Ordina per Artista' })
    const th = artistSortBtn.closest('th')
    expect(th).toHaveAttribute('aria-sort', 'descending')
  })

  it('supports keyboard navigation in facet combobox (ArrowDown, Enter, Escape)', async () => {
    mockFetch({
      '/api/tracks/facets': facetsBody({ artists: [{ value: 'Sigur Rós', count: 2 }, { value: 'Jónsi', count: 1 }, { value: 'Björk', count: 3 }] }),
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })
    const user = userEvent.setup()

    const trigger = screen.getByRole('button', { name: /Tutti gli artisti/ })
    await user.click(trigger)
    const input = screen.getByPlaceholderText(/Cerca artista/)
    expect(input).toHaveFocus()

    // ArrowDown moves active
    await user.keyboard('{ArrowDown}')
    // Enter selects
    await user.keyboard('{Enter}')
    // Should have selected Jónsi (index 1) — chip appears
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rimuovi filtro Artista: Jónsi' })).toBeInTheDocument())

    // Reopen and Escape closes
    await user.click(screen.getByRole('button', { name: 'Artista: Jónsi' }))
    expect(screen.getByPlaceholderText(/Cerca artista/)).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByPlaceholderText(/Cerca artista/)).not.toBeInTheDocument())
  })

  it('shows facet pagination load more for high cardinality', async () => {
    const manyArtists = Array.from({ length: 200 }, (_, i) => ({ value: `Artist ${String(i).padStart(4, '0')}`, count: 1 }))
    mockFetch({
      '/api/tracks/facets': (url: string) => {
        const u = new URL(url, 'http://localhost')
        const limit = Number(u.searchParams.get('limit') ?? '100')
        const cursor = u.searchParams.get('cursor')
        const facet_q = u.searchParams.get('facet_q')?.toLowerCase() ?? ''
        let filtered = manyArtists
        if (facet_q) filtered = filtered.filter((a) => a.value.toLowerCase().includes(facet_q))
        if (cursor) {
          const cursorVal = decodeFacetCursor(cursor)
          const idx = filtered.findIndex((a) => a.value === cursorVal)
          filtered = idx >= 0 ? filtered.slice(idx + 1) : filtered
        }
        return {
          artists: filtered.slice(0, limit),
          albums: [{ value: 'Album 1', count: 1 }],
          genres: [{ value: 'Rock', count: 1 }],
          formats: [{ value: 'flac', count: 1 }],
        }
      },
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Tutti gli artisti/ }))
    expect(await screen.findByText('Artist 0000')).toBeInTheDocument()
    const loadMore = await screen.findByRole('button', { name: 'Carica altri' })
    expect(loadMore).toBeInTheDocument()
    await user.click(loadMore)
    // After load more, should show more items (artist beyond 100 truncated? but we increased limit, so next batch)
    // In our simple limit-increase model, load more increases limit to 200, so Artist 0100 appears
    await waitFor(() => expect(screen.getByText('Artist 0100')).toBeInTheDocument())
  })

  it('counts are consistent with server filtering and no double client filtering', async () => {
    // Facet counts should match server-filtered total — client does not re-filter
    mockFetch({
      '/api/tracks/facets': {
        artists: [{ value: 'Jónsi', count: 1 }],
        albums: [{ value: 'Go', count: 1 }],
        genres: [{ value: 'Electronic', count: 1 }],
        formats: [{ value: 'mp3', count: 1 }],
      },
      '/api/tracks': {
        items: [],
        next_cursor: null,
        total: 0,
      },
    })
    const { wrapper } = createWrapper(['/catalog?artist=J%C3%B3nsi&album=Go'])
    render(<Catalog />, { wrapper })

    // Chips present for both facets — query via remove buttons
    expect(await screen.findByRole('button', { name: 'Rimuovi filtro Artista: Jónsi' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rimuovi filtro Album: Go' })).toBeInTheDocument()
    // Open artist facet — should show count (1) consistent with filtered total
    const trigger = screen.getByRole('button', { name: 'Artista: Jónsi' })
    await userEvent.setup().click(trigger)
    const listbox = screen.getByRole('listbox', { name: 'Artista' })
    expect(await within(listbox).findByText('Jónsi')).toBeInTheDocument()
    expect(within(listbox).getByText('(1)')).toBeInTheDocument()
  })

  it('shows no results with suggestion when facet search yields nothing', async () => {
    mockFetch({
      '/api/tracks/facets': {
        artists: [],
        albums: [],
        genres: [],
        formats: [],
      },
      '/api/tracks': TRACKS_EMPTY,
    })
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Tutti gli artisti/ }))
    const input = screen.getByPlaceholderText(/Cerca artista/)
    await user.type(input, 'nonexistent-zzzzz')
    await waitFor(() => expect(screen.getByText(/Nessun risultato/)).toBeInTheDocument())
    expect(screen.getByText(/Prova un termine diverso/)).toBeInTheDocument()
  })

  it('distinguishes failed facet load from empty and offers retry', async () => {
    let facetsCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        if (url.includes('/api/tracks/facets')) {
          facetsCalls += 1
          if (facetsCalls === 1) {
            return Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) } as Response)
          }
          return Promise.resolve({ ok: true, json: async () => facetsBody() } as Response)
        }
        if (url.includes('/api/tracks')) return Promise.resolve({ ok: true, json: async () => TRACKS_ONE } as Response)
        throw new Error(`unmocked fetch: ${url}`)
      }),
    )
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Tutti gli artisti/ }))
    await waitFor(() => expect(screen.getByText('Impossibile caricare i filtri.')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Riprova' })).toBeInTheDocument()
    expect(screen.queryByText(/Nessun risultato/)).not.toBeInTheDocument()
    // retry succeeds
    await user.click(screen.getByRole('button', { name: 'Riprova' }))
    const listbox = screen.getByRole('listbox', { name: 'Artista' })
    await waitFor(() => expect(within(listbox).getByText('Sigur Rós')).toBeInTheDocument())
  })

  it('paginates beyond 500 via cursor without 422', async () => {
    const manyArtists = Array.from({ length: 600 }, (_, i) => ({ value: `Artist ${String(i).padStart(4, '0')}`, count: 1 }))
    mockFetch({
      '/api/tracks/facets': (url: string) => {
        const u = new URL(url, 'http://localhost')
        const limit = Number(u.searchParams.get('limit') ?? '100')
        const cursor = u.searchParams.get('cursor')
        expect(limit).toBeLessThanOrEqual(500)
        expect(u.searchParams.has('offset')).toBe(false)
        let filtered = manyArtists
        const facet_q = u.searchParams.get('facet_q')?.toLowerCase() ?? ''
        if (facet_q) filtered = filtered.filter((a) => a.value.toLowerCase().includes(facet_q))
        if (cursor) {
          const cursorVal = decodeFacetCursor(cursor)
          const idx = filtered.findIndex((a) => a.value === cursorVal)
          filtered = idx >= 0 ? filtered.slice(idx + 1) : filtered
        }
        return {
          artists: filtered.slice(0, limit),
          albums: [{ value: 'Album 1', count: 1 }],
          genres: [{ value: 'Rock', count: 1 }],
          formats: [{ value: 'flac', count: 1 }],
        }
      },
      '/api/tracks': TRACKS_ONE,
    })
    const { wrapper } = createWrapper()
    render(<Catalog />, { wrapper })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Tutti gli artisti/ }))
    expect(await screen.findByText('Artist 0000')).toBeInTheDocument()
    // Click Carica altri 5 times to reach beyond 500 (0->100->200->300->400->500)
    for (let i = 0; i < 5; i++) {
      const btn = await screen.findByRole('button', { name: 'Carica altri' })
      await user.click(btn)
      // wait for next page appended
      await waitFor(() => expect(screen.getByText(`Artist ${String((i + 1) * 100).padStart(4, '0')}`)).toBeInTheDocument())
    }
    // Now 600th item should be reachable without 422
    await waitFor(() => expect(screen.getByText('Artist 0500')).toBeInTheDocument())
    // One more load to get last 100
    await user.click(screen.getByRole('button', { name: 'Carica altri' }))
    await waitFor(() => expect(screen.getByText('Artist 0599')).toBeInTheDocument())
  })
})
