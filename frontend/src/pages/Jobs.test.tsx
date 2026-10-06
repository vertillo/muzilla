import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/hooks/useToasts'
import type { ActivityItem } from '@/lib/api'
import { Jobs } from '@/pages/Jobs'

function createWrapper(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ToastProvider>{children}</ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

const wrapper = createWrapper()

function undoActivity(files: unknown[]): ActivityItem {
  return {
    id: 'undo:91',
    kind: 'undo',
    title: 'Undo review #17',
    state: 'failed',
    created_at: '2026-10-06T00:00:00Z',
    updated_at: '2026-10-06T00:00:00Z',
    job_id: null,
    import_session_id: null,
    review_bundle_id: 17,
    apply_run_id: 16,
    undo_run_id: 18,
    progress_current: null,
    progress_total: null,
    progress_message: null,
    error: 'recovery required',
    result: { state: 'failed', recovery_required: true, files },
    cancellable: false,
  }
}

function stubActivity(item: ActivityItem) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      const body = url.startsWith('/api/activity')
        ? { items: [item], next_cursor: null }
        : {}
      return Promise.resolve({ ok: true, status: 200, statusText: 'OK', json: async () => body })
    }),
  )
}

describe('Jobs undo activity summary', () => {
  afterEach(() => vi.unstubAllGlobals())

  async function expandUndoSummary(files: unknown[]) {
    stubActivity(undoActivity(files))
    render(<Jobs />, { wrapper: createWrapper() })
    const title = await screen.findByText('Undo review #17')
    const row = title.closest('[role="button"]')
    expect(row).not.toBeNull()
    fireEvent.click(row!)
  }

  it('counts only fully undone files as restored in a partial result', async () => {
    await expandUndoSummary([{ track_id: 2, state: 'undone' }, { track_id: 3, state: 'failed' }])

    expect(await screen.findByText('File ripristinati: 1 • falliti: 1')).toBeInTheDocument()
    expect(screen.queryByText('File ripristinati: 2')).not.toBeInTheDocument()
  })

  it('distinguishes failed, pending, skipped, and unknown file outcomes', async () => {
    await expandUndoSummary([
      { track_id: 1, state: 'undone' },
      { track_id: 2, state: 'failed' },
      { track_id: 3, state: 'pending' },
      { track_id: 4, state: 'skipped' },
      { track_id: 5, state: 'rolled_back' },
      null,
    ])

    expect(
      await screen.findByText(
        'File ripristinati: 1 • falliti: 1 • in sospeso: 1 • saltati: 1 • esito sconosciuto: 2',
      ),
    ).toBeInTheDocument()
  })
})

describe('Jobs ReplayGain capability', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('does not offer ReplayGain when the runtime probe reports it unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        const body = url.startsWith('/api/capabilities')
          ? {
              replaygain: {
                name: 'replaygain',
                state: 'unavailable',
                enabled: true,
                available: false,
                detail: 'rsgain executable could not start',
              },
            }
          : { items: [], next_cursor: null }
        return Promise.resolve({ ok: true, json: async () => body })
      }),
    )

    render(<Jobs />, { wrapper })

    const button = screen.getByRole('button', { name: 'ReplayGain' })
    await waitFor(() => expect(button).toBeDisabled())
    expect(
      await screen.findByText(
        'ReplayGain unavailable: rsgain executable could not start. Check runtime diagnostics and configuration, then retry.',
      ),
    ).toBeInTheDocument()
  })

  it('disables ReplayGain when a background capability refresh fails', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let capabilityCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        if (!url.startsWith('/api/capabilities')) {
          return Promise.resolve({ ok: true, json: async () => ({ items: [], next_cursor: null }) })
        }

        capabilityCalls += 1
        if (capabilityCalls === 1) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              replaygain: {
                name: 'replaygain',
                state: 'available',
                enabled: true,
                available: true,
                detail: 'operational',
              },
            }),
          })
        }
        return Promise.resolve({ ok: false, status: 503, statusText: 'Service Unavailable', json: async () => ({}) })
      }),
    )

    render(<Jobs />, { wrapper: createWrapper(client) })

    const button = screen.getByRole('button', { name: 'ReplayGain' })
    await waitFor(() => expect(button).toBeEnabled())

    await act(async () => {
      await client.invalidateQueries({ queryKey: ['runtime-capabilities'] })
    })

    await waitFor(() => expect(button).toBeDisabled())
    expect(await screen.findByText('ReplayGain availability unknown')).toBeInTheDocument()
  })
})
