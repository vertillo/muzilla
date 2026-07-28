import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { ProviderHealthPanel } from '@/components/ProviderHealthPanel'
import type { ProviderStatus, ProviderStatusList } from '@/lib/types'

function status(overrides: Partial<ProviderStatus>): ProviderStatus {
  return {
    provider: 'musicbrainz',
    enabled: true,
    requires_auth: false,
    token_configured: true,
    live: true,
    last_success_at: null,
    last_error_at: null,
    last_error_detail: null,
    rate_limited: false,
    ...overrides,
  }
}

function mockFetch(body: ProviderStatusList) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => body,
    }),
  )
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('ProviderHealthPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a healthy status for a provider with a recent success', async () => {
    mockFetch({
      items: [status({ provider: 'musicbrainz', last_success_at: '2026-01-01T00:00:00Z' })],
    })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('healthy')).toBeInTheDocument())
    expect(screen.getByText('musicbrainz')).toBeInTheDocument()
  })

  it('shows a rate-limited status distinctly from a generic error', async () => {
    mockFetch({
      items: [
        status({
          provider: 'musicbrainz',
          rate_limited: true,
          last_error_at: '2026-01-01T00:00:00Z',
          last_error_detail: 'rate limited (HTTP 429)',
        }),
      ],
    })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('rate limited')).toBeInTheDocument())
  })

  it('shows a missing-token status for an auth-required provider with no token', async () => {
    mockFetch({
      items: [
        status({
          provider: 'discogs',
          requires_auth: true,
          token_configured: false,
          live: false,
        }),
      ],
    })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('missing token')).toBeInTheDocument())
  })

  it('shows a disabled status for a provider turned off in config', async () => {
    mockFetch({ items: [status({ provider: 'discogs', enabled: false })] })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('disabled')).toBeInTheDocument())
  })

  it('shows an error status when the last recorded event was a failure', async () => {
    mockFetch({
      items: [
        status({
          provider: 'deezer',
          last_success_at: '2026-01-01T00:00:00Z',
          last_error_at: '2026-01-02T00:00:00Z',
          last_error_detail: 'HTTP 503',
        }),
      ],
    })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('error')).toBeInTheDocument())
  })

  it('shows an unknown status for a provider never called this process', async () => {
    mockFetch({ items: [status({ provider: 'lrclib' })] })
    render(<ProviderHealthPanel />, { wrapper })

    await waitFor(() => expect(screen.getByText('unknown')).toBeInTheDocument())
  })
})
