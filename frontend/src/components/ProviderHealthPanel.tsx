import { Badge, type BadgeTone } from '@/components/ui'
import { useProviderStatus } from '@/hooks/useProviderStatus'
import type { ProviderStatus } from '@/lib/types'

/** Provider health indicator (Phase 7 suggestion #7: "nothing in the
 * UI shows whether MusicBrainz is rate-limiting or a token is
 * missing"). Used by the Dashboard; shared here (rather than a
 * Dashboard-local component) so a future second consumer reuses the
 * same query and rendering instead of building a disconnected copy. */

function statusFor(p: ProviderStatus): { tone: BadgeTone; label: string } {
  if (!p.enabled) return { tone: 'neutral', label: 'disabled' }
  if (p.requires_auth && !p.token_configured) return { tone: 'conflict', label: 'missing token' }
  if (p.rate_limited) return { tone: 'conflict', label: 'rate limited' }
  if (p.last_error_at && (!p.last_success_at || p.last_error_at > p.last_success_at)) {
    return { tone: 'removed', label: 'error' }
  }
  if (p.last_success_at) return { tone: 'added', label: 'healthy' }
  return { tone: 'neutral', label: 'unknown' }
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
