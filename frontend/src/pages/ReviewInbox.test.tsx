import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { ReviewInbox } from '@/pages/ReviewInbox'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}><MemoryRouter initialEntries={['/reviews?q=source']}>{children}</MemoryRouter></QueryClientProvider>
}

describe('ReviewInbox', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders filename, full path, textual state, confidence and error without hover', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      items: [{ id: 7, title: 'Review source', state: 'needs_attention', filename: '01-source.flac', path: '/music/incoming/01-source.flac', format: 'flac', candidate_source: 'musicbrainz', confidence: null, confidence_label: 'Needs attention', cover_thumbnail_url: null, issues: [{ kind: 'task', message: 'Lyrics temporarily unavailable' }], accepted_operations: 0, pending_operations: 2, rejected_operations: 0 }], total: 1,
    }) }))

    render(<ReviewInbox />, { wrapper })

    await waitFor(() => expect(screen.getByText('01-source.flac')).toBeInTheDocument())
    expect(screen.getByText('/music/incoming/01-source.flac')).toBeInTheDocument()
    expect(screen.getAllByText('Richiede attenzione').at(-1)).toBeInTheDocument()
    expect(screen.getByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText('Problema: Lyrics temporarily unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apri revisione: 01-source.flac/ })).toBeInTheDocument()
  })

  it('archives a fully rejected review and sends the current revision token', async () => {
    let rejected = false
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/reviews?')) {
        return Promise.resolve({ ok: true, json: async () => ({
          items: rejected ? [] : [{ id: 7, title: 'Review source', state: 'ready', filename: '01-source.flac', path: '/music/incoming/01-source.flac', format: 'flac', candidate_source: 'musicbrainz', confidence: null, confidence_label: 'Not scored', cover_thumbnail_url: null, issues: [], accepted_operations: 0, pending_operations: 1, rejected_operations: 0 }], total: rejected ? 0 : 1,
        }) })
      }
      if (url === '/api/reviews/7/operations' && init?.method === 'PATCH') {
        rejected = true
        return Promise.resolve({ ok: true, json: async () => ({ state: 'discarded' }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({
        id: 7,
        current_revision: { id: 3, operations: [{ id: 11, decision: 'pending' }] },
      }) })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ReviewInbox />, { wrapper })
    await screen.findByRole('button', { name: 'Rifiuta proposte' })
    fireEvent.click(screen.getByRole('button', { name: 'Rifiuta proposte' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: /Apri revisione/ })).not.toBeInTheDocument())
    const patch = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
    if (!patch) throw new Error('review decision request was not sent')
    expect(JSON.parse((patch[1] as RequestInit).body as string)).toEqual({
      revision_id: 3,
      decisions: [{ operation_id: 11, decision: 'rejected' }],
    })
  })
})
