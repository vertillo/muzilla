import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Dashboard } from '@/pages/Dashboard'

function jsonResponse(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body })
}

function mockFetchByUrl(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      const match = Object.keys(routes).find((k) => url.startsWith(k))
      if (!match) throw new Error(`unmocked fetch: ${url}`)
      return jsonResponse(routes[match])
    }),
  )
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Dashboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders library counts, recent changesets/jobs, and provider health once loaded', async () => {
    mockFetchByUrl({
      '/api/dashboard/summary': {
        total_tracks: 42,
        tracks_missing: 1,
        tracks_with_errors: 2,
        tracks_missing_art: 5,
        album_count: 6,
        singleton_count: 10,
        ungrouped_track_count: 3,
      },
      '/api/changesets': {
        items: [
          {
            id: 7,
            title: 'Sigur Rós — Ágætis byrjun',
            source: 'match_proposal',
            state: 'draft',
            scope_type: 'group',
            scope_id: 1,
            created_by: 'system',
            candidate_source: null,
            candidate_ref: null,
            undo_of_id: null,
            stats: {},
            error: null,
          },
        ],
        next_cursor: null,
        total: 1,
      },
      '/api/jobs': {
        items: [
          {
            id: 3,
            type: 'scan',
            state: 'succeeded',
            priority: 0,
            progress_current: 1,
            progress_total: 1,
            progress_message: null,
            attempts: 1,
            error: null,
          },
        ],
        next_cursor: null,
      },
      '/api/providers/status': {
        items: [
          {
            provider: 'musicbrainz',
            enabled: true,
            requires_auth: false,
            token_configured: true,
            live: true,
            last_success_at: '2026-01-01T00:00:00Z',
            last_error_at: null,
            last_error_detail: null,
            rate_limited: false,
          },
        ],
      },
    })

    render(<Dashboard />, { wrapper })

    await waitFor(() => expect(screen.getByText('42')).toBeInTheDocument())
    expect(screen.getByText('6')).toBeInTheDocument() // album_count
    expect(screen.getByText('10')).toBeInTheDocument() // singleton_count

    await waitFor(() => expect(screen.getByText(/Ágætis byrjun/)).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/scan/)).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('healthy')).toBeInTheDocument())
  })

  it('shows an error state instead of blank tiles when the summary request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: 'boom' }) }),
    )

    render(<Dashboard />, { wrapper })

    await waitFor(() => expect(screen.getByText("Couldn't load library summary")).toBeInTheDocument())
  })
})
