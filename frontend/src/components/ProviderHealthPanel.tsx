import { Badge, type BadgeTone } from '@/components/ui'
import { useProviderStatus } from '@/hooks/useProviderStatus'
import type { ProviderStatus } from '@/lib/types'

/** Provider health indicator: shows whether MusicBrainz is rate-limiting or a token is
 * missing"). Used by the Dashboard; shared here (rather than a
 * Dashboard-local component) so a future second consumer reuses the
 * same query and rendering instead of building a disconnected copy. */

function statusFor(p: ProviderStatus): { tone: BadgeTone; label: string } {
  // Keep the dashboard usable through a rolling deploy where an older API
  // response has not gained `state` yet; this fallback is deliberately one
  // of the explicit states, never the former generic "unknown" label.
  const state = p.state ?? legacyState(p)
  switch (state) {
    case 'disabled': return { tone: 'neutral', label: 'disabled' }
    case 'not_configured': return { tone: 'conflict', label: 'not configured' }
    case 'checking': return { tone: 'neutral', label: 'checking' }
    case 'operational': return { tone: 'added', label: 'operational' }
    case 'temporary_unavailable': return { tone: 'removed', label: 'temporarily unavailable' }
    case 'invalid_credentials': return { tone: 'conflict', label: 'invalid credentials' }
  }
}

function legacyState(p: ProviderStatus): ProviderStatus['state'] {
  if (!p.enabled) return 'disabled'
  if (p.requires_auth && !p.token_configured) return 'not_configured'
  if (p.rate_limited || (p.last_error_at && (!p.last_success_at || p.last_error_at > p.last_success_at))) {
    return 'temporary_unavailable'
  }
  return p.last_success_at ? 'operational' : 'checking'
}

export function ProviderHealthPanel() {
  const { data, isLoading, isError } = useProviderStatus()

  if (isLoading) {
    return <span className="text-xs text-text-muted">Loading…</span>
  }
  if (isError || !data) {
    return <span className="text-xs text-text-muted">Couldn't load provider status.</span>
  }

  return (
    <div className="flex flex-col gap-2">
      {data.items.map((p) => {
        const { tone, label } = statusFor(p)
        return (
          <div key={p.provider} className="flex items-center justify-between gap-3">
            <span className="text-sm capitalize">{p.provider}</span>
            <span
              role="img"
              aria-label={`${p.provider}: ${label}${p.last_error_detail ? ` (${p.last_error_detail})` : ''}`}
              title={p.last_error_detail ?? label}
            >
              <Badge tone={tone} dot>
                {label}
              </Badge>
            </span>
          </div>
        )
      })}
    </div>
  )
}
