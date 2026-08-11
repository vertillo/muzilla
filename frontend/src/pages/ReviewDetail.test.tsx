import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { ReviewDetail } from '@/pages/ReviewDetail'
import { ToastProvider } from '@/hooks/useToasts'

const review = {
  id: 7,
  logical_key: 'track:7',
  title: 'Review 01-source.flac',
  scope_type: 'track',
  scope_id: 7,
  state: 'ready',
  error: null,
  source_items: [{ source_id: 7, filename: '01-source.flac', path: '/music/incoming/01-source.flac', format: 'flac' }],
  cover_candidates: [{ id: 41, blob_id: 77, provider: 'upload', mime: 'image/jpeg', size: 123, width: 300, height: 300, thumbnail_url: '/api/reviews/7/cover/candidates/41/thumbnail' }],
  task_attempts: [],
  apply_runs: [],
  current_revision: {
    id: 3, revision_no: 1, content_digest: 'digest', candidate_source: 'musicbrainz', candidate_ref: 'release-7', candidate_snapshot: { artist: 'Artist', title: 'Track', album: 'Album', year: 2026, duration_ms: 123000, position: 1, track_count: 10, signals: ['title exact'], penalties: [] }, match_explanation: { rejection_reasons: [] }, confidence: 0.92, created_at: '2026-08-01T00:00:00Z',
    operations: [
      { id: 11, seq: 1, kind: 'set_tag', field: 'title', target_type: 'track', target_id: 7, current_value: 'Old title', proposed_value: 'New title', decision: 'pending', provenance: {}, validation: {} },
      { id: 12, seq: 2, kind: 'move_file', field: 'path', target_type: 'track', target_id: 7, current_value: '/music/incoming/01-source.flac', proposed_value: '/music/New title.flac', decision: 'accepted', provenance: {}, validation: {} },
    ],
  },
}

const page = {
  items: [{ id: 7, title: review.title, state: 'ready', filename: '01-source.flac', path: '/music/incoming/01-source.flac', format: 'flac', candidate_source: 'musicbrainz', confidence: null, confidence_label: 'Not scored', cover_thumbnail_url: null, issues: [], accepted_operations: 1, pending_operations: 1, rejected_operations: 0 }],
  total: 1,
}

const postCoverReview = {
  ...review,
  current_revision: {
    ...review.current_revision,
    id: 4,
    revision_no: 2,
    candidate_snapshot: { ...review.current_revision.candidate_snapshot, artist: 'Post-action artist', title: 'Post-action track', signals: ['cover preserved candidate'] },
    match_explanation: { rejection_reasons: ['post-action explanation'] },
    confidence: 0.81,
  },
}

function mockFetch({ coverResponse = review }: { coverResponse?: typeof postCoverReview } = {}) {
  let coverApplied = false
  const fetch = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/reviews/7/cover') coverApplied = true
    const body = url.endsWith('/neighbors')
      ? { previous_id: 6, next_id: 8, next_unreviewed_id: 8 }
      : url === '/api/reviews/7/cover' ? coverResponse
      : /^\/api\/reviews\/\d+$/.test(url) ? (coverApplied ? coverResponse : review) : page
    return Promise.resolve({ ok: true, json: async () => body })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}><ToastProvider><MemoryRouter initialEntries={['/reviews/7?returnTo=%2Freviews']}><Routes><Route path="/reviews/:id" element={children} /></Routes></MemoryRouter></ToastProvider></QueryClientProvider>
}

describe('ReviewDetail', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('keeps source identity visible and moves roving focus with J/K without acting through a dialog', async () => {
    mockFetch()
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: scrollIntoView, configurable: true })
    render(<ReviewDetail />, { wrapper })

    await waitFor(() => expect(screen.getByText('01-source.flac')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Successiva' })).toBeEnabled())
    expect(screen.getAllByText('/music/incoming/01-source.flac')[0]).toBeInTheDocument()
    expect(screen.getAllByText('Accetta')).toHaveLength(2)
    expect(screen.getAllByText('Rifiuta')).toHaveLength(2)

    fireEvent.keyDown(screen.getByText('Old title'), { key: 'j' })
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Scorciatoie' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(dialog, { key: 'j' })
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('autosaves one decision from a multi-operation review without sending its siblings', async () => {
    const fetch = mockFetch()
    render(<ReviewDetail />, { wrapper })

    await screen.findByText('Old title')
    fireEvent.click(screen.getAllByRole('button', { name: 'Accetta' })[0])

    await waitFor(() => {
      const patch = fetch.mock.calls.find(
        ([url, init]) => String(url) === '/api/reviews/7/operations' && init?.method === 'PATCH',
      )
      expect(patch).toBeDefined()
      if (!patch) throw new Error('Expected an operation-decision request')
      const init = patch[1]
      if (!init || typeof init.body !== 'string') throw new Error('Expected a JSON request body')
      expect(JSON.parse(init.body)).toEqual({
        revision_id: 3,
        decisions: [{ operation_id: 11, decision: 'accepted' }],
      })
    })
  })

  it('renders the candidate snapshot and sends typed cover and tag-edit mutations', async () => {
    const fetch = mockFetch()
    render(<ReviewDetail />, { wrapper })

    await screen.findByText('Artist — Track')
    expect(screen.getByText(/Confidenza 92%/)).toBeInTheDocument()
    expect(screen.getByText(/Perché: title exact/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Usa' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Modifica' })[0])
    fireEvent.change(await screen.findByLabelText('Valore tag'), { target: { value: 'Edited title' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salva modifica' }))

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith('/api/reviews/7/cover', expect.objectContaining({ method: 'POST' }))
      const edit = fetch.mock.calls.find(([url, init]) => String(url) === '/api/reviews/7/operations/11/edit' && init?.method === 'POST')
      expect(edit).toBeDefined()
      if (!edit) throw new Error('Expected typed edit request')
      const init = edit[1]
      if (!init || typeof init.body !== 'string') throw new Error('Expected a JSON request body')
      expect(JSON.parse(init.body)).toEqual({ revision_id: 3, kind: 'set_tag', value: 'Edited title' })
    })
  })

  it('keeps the CandidateCard visible after a cover action returns a successor revision', async () => {
    mockFetch({ coverResponse: postCoverReview })
    render(<ReviewDetail />, { wrapper })

    await screen.findByText('Artist — Track')
    fireEvent.click(screen.getByRole('button', { name: 'Usa' }))

    await screen.findByText('Post-action artist — Post-action track')
    expect(screen.getByText(/Confidenza 81%/)).toBeInTheDocument()
    expect(screen.getByText(/Perché: cover preserved candidate/)).toBeInTheDocument()
  })

  it('uses neighbors for a directly opened review beyond the first inbox page', async () => {
    const fetch = mockFetch()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <MemoryRouter initialEntries={['/reviews/101?returnTo=%2Freviews']}>
            <Routes><Route path="/reviews/:id" element={<ReviewDetail />} /></Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    )

    await waitFor(() => expect(screen.getByRole('button', { name: 'Successiva' })).toBeEnabled())
    expect(fetch).toHaveBeenCalledWith('/api/reviews/101/neighbors', expect.any(Object))
  })
})
