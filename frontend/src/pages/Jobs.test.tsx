import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/hooks/useToasts'
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
