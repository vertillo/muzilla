import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge, EmptyState, SkeletonRows, type BadgeTone } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { ProviderHealthPanel } from '@/components/ProviderHealthPanel'
import { useDashboardSummary } from '@/hooks/useDashboard'
import { useRecentImportSessions } from '@/hooks/useImports'
import { useReviewInbox } from '@/hooks/useReviews'
import type { ImportSessionState, ReviewBundleSummary } from '@/lib/types'

const REVIEW_TONE: Record<ReviewBundleSummary['state'], BadgeTone> = { preparing: 'neutral', ready: 'added', needs_attention: 'conflict', applying: 'accent', applied: 'added', partially_applied: 'conflict', failed: 'removed', discarded: 'neutral' }
const IMPORT_TONE: Record<ImportSessionState, BadgeTone> = { pending: 'neutral', scanning: 'accent', fingerprinting: 'accent', grouping: 'accent', matching: 'accent', reviewing: 'accent', completed: 'added', failed: 'removed', cancelled: 'conflict' }

function StatTile({ label, value, to }: { label: string; value: number | string; to?: string }) {
  const content = <><div className="text-2xl font-semibold font-mono">{value}</div><div className="text-xs text-text-muted mt-2">{label}</div></>
  return to ? <Link to={to} className="focus-ring flex-1 min-w-[140px] rounded-md border border-border-subtle bg-surface-raised p-4 text-inherit no-underline">{content}</Link> : (
    <div className="flex-1 min-w-[140px] p-4 border border-border-subtle rounded-md bg-surface-raised">
      {content}
    </div>
  )
}

function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex-[1_1_320px] min-w-[300px] border border-border-subtle rounded-md p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold m-0">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  )
}

export function Dashboard() {
  const summary = useDashboardSummary()
  const recentReviews = useReviewInbox({ limit: 5 })
  const recentImports = useRecentImportSessions()

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader title="Dashboard" />

      <div className="p-5 flex flex-col gap-5">
        {summary.isError ? (
          <EmptyState
            title="Couldn't load library summary"
            description="The server returned an error."
          />
        ) : summary.isLoading || !summary.data ? (
          <div className="flex gap-3 flex-wrap">
            {Array.from({ length: 7 }, (_, i) => (
              <div
                key={i}
                className="flex-1 min-w-[140px] h-[64px] rounded-md bg-surface-raised"
                style={{ animation: 'var(--skeleton-pulse)' }}
              />
            ))}
          </div>
        ) : (
          <div className="flex gap-3 flex-wrap">
            <StatTile label="File nel catalogo" value={summary.data.total_tracks} to="/catalog" />
            <StatTile label="Albums" value={summary.data.album_count} />
            <StatTile label="Singles" value={summary.data.singleton_count} />
            <StatTile label="Ungrouped" value={summary.data.ungrouped_track_count} />
            <StatTile label="Problemi da risolvere" value={summary.data.tracks_with_errors} to="/catalog?status=errored" />
            <StatTile label="Missing art" value={summary.data.tracks_missing_art} />
            <StatTile label="File mancanti" value={summary.data.tracks_missing} to="/catalog?status=missing" />
          </div>
        )}

        <div className="flex gap-4 flex-wrap">
          <Panel
            title="Revisioni recenti"
            action={
              <Link to="/reviews" className="text-xs text-accent-text">
                Apri revisioni
              </Link>
            }
          >
            {recentReviews.isError ? (
              <span className="text-xs text-text-muted">Impossibile caricare le revisioni.</span>
            ) : recentReviews.isLoading ? (
              <SkeletonRows count={3} />
            ) : (recentReviews.data?.pages[0]?.items.length ?? 0) === 0 ? (
              <span className="text-xs text-text-muted">Nessuna revisione da controllare.</span>
            ) : (
              <div className="flex flex-col gap-2">
                {recentReviews.data?.pages[0]?.items.map((review) => (
                  <Link
                    key={review.id}
                    to={`/reviews/${review.id}?returnTo=%2F`}
                    className="flex items-center justify-between gap-3 text-inherit no-underline"
                  >
                    <span className="overflow-hidden text-ellipsis whitespace-nowrap text-sm">
                      #{review.id} {review.filename ?? review.title}
                    </span>
                    <Badge tone={REVIEW_TONE[review.state]}>{review.state}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Sessioni recenti"
            action={
              <Link to="/activity" className="text-xs text-accent-text">
                Apri attività
              </Link>
            }
          >
            {recentImports.isError ? (
              <span className="text-xs text-text-muted">Impossibile caricare le sessioni.</span>
            ) : recentImports.isLoading ? (
              <SkeletonRows count={3} />
            ) : !recentImports.data || recentImports.data.items.length === 0 ? (
              <span className="text-xs text-text-muted">Nessuna sessione di import.</span>
            ) : (
              <div className="flex flex-col gap-2">
                {recentImports.data.items.map((session) => (
                  <Link key={session.id} to={`/import/${session.id}`} className="flex items-center justify-between gap-3 text-inherit no-underline">
                    <span className="text-sm">
                      #{session.id} Import
                    </span>
                    <Badge tone={IMPORT_TONE[session.state]}>{session.state}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Provider health">
            <ProviderHealthPanel />
          </Panel>
        </div>
      </div>
    </div>
  )
}
