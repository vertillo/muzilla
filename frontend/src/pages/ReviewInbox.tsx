import { useEffect, useMemo } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Badge, Button, EmptyState, SkeletonRows, ThumbnailTile } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { getReviewBundle, patchReviewOperationDecisions } from '@/lib/api'
import { useReviewInbox } from '@/hooks/useReviews'
import type { ReviewBundleSummary } from '@/lib/types'

const STATE_LABEL: Record<ReviewBundleSummary['state'], string> = {
  preparing: 'In preparazione',
  ready: 'Pronta',
  needs_attention: 'Richiede attenzione',
  applying: 'Applicazione in corso',
  applied: 'Applicata',
  partially_applied: 'Parzialmente applicata',
  failed: 'Fallita',
  discarded: 'Archiviata',
}

function toneForState(state: ReviewBundleSummary['state']) {
  if (state === 'needs_attention' || state === 'failed' || state === 'partially_applied') return 'conflict' as const
  if (state === 'ready' || state === 'applied') return 'added' as const
  return 'neutral' as const
}

function updateSearch(
  current: URLSearchParams,
  setSearch: (next: URLSearchParams) => void,
  name: string,
  value: string,
) {
  const next = new URLSearchParams(current)
  if (value) next.set(name, value)
  else next.delete(name)
  setSearch(next)
}

export function ReviewInbox() {
  const [search, setSearch] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const filters = useMemo(() => ({
    q: search.get('q') ?? undefined,
    state: search.get('state')?.split(',').filter(Boolean),
    confidence: search.get('confidence') ?? undefined,
    issue: search.get('issue') ?? undefined,
    source: search.get('source') ?? undefined,
  }), [search])
  const inbox = useReviewInbox(filters)
  const reject = useMutation({
    mutationFn: async (reviewId: number) => {
      const review = await getReviewBundle(reviewId)
      return patchReviewOperationDecisions(
        reviewId,
        review.current_revision.id,
        review.current_revision.operations.map((operation) => ({ operation_id: operation.id, decision: 'rejected' })),
      )
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['reviews'] }),
  })

  useEffect(() => {
    const anchor = location.hash.slice(1)
    if (!anchor || !inbox.data) return
    requestAnimationFrame(() => document.getElementById(anchor)?.scrollIntoView({ block: 'nearest' }))
  }, [inbox.data, location.hash])

  const open = (id: number) => {
    const returnTo = `${location.pathname}${location.search}#review-${id}`
    navigate(`/reviews/${id}?returnTo=${encodeURIComponent(returnTo)}`)
  }
  const clearFilters = () => setSearch(new URLSearchParams())
  const activeFilterCount = ['state', 'confidence', 'issue', 'source'].filter((key) => search.has(key)).length

  return (
    <div className="min-h-0">
      <PageHeader title="Revisioni">
        <p className="mt-1 text-sm text-text-secondary">Controlla le proposte prima che qualsiasi file venga modificato.</p>
      </PageHeader>

      <div className="border-b border-border-subtle p-4 sm:p-5">
        <label className="block max-w-2xl text-sm font-medium text-text-primary">
          Cerca file, percorso, artista, titolo, provider o errore
          <input
            value={search.get('q') ?? ''}
            onChange={(event) => updateSearch(search, setSearch, 'q', event.target.value)}
            className="mt-2 min-h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm text-text-primary"
            placeholder="es. oxygen, /singles, MusicBrainz"
          />
        </label>
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Filtri revisioni">
          <label className="text-sm text-text-secondary">
            Stato
            <select
              value={search.get('state') ?? ''}
              onChange={(event) => updateSearch(search, setSearch, 'state', event.target.value)}
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="ready">Pronte</option>
              <option value="needs_attention">Richiede attenzione</option>
              <option value="preparing">In preparazione</option>
              <option value="failed">Fallite</option>
              <option value="applied,discarded">Applicate o archiviate</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Problema
            <select
              value={search.get('issue') ?? ''}
              onChange={(event) => updateSearch(search, setSearch, 'issue', event.target.value)}
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="review">Review</option>
              <option value="task">Enrichment</option>
              <option value="collision">Collisione percorso</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Provider
            <select
              value={search.get('source') ?? ''}
              onChange={(event) => updateSearch(search, setSearch, 'source', event.target.value)}
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="musicbrainz">MusicBrainz</option>
              <option value="discogs">Discogs</option>
              <option value="deezer">Deezer</option>
            </select>
          </label>
          {activeFilterCount > 0 && <Button variant="ghost" size="sm" onClick={clearFilters}>Cancella filtri</Button>}
        </div>
      </div>

      {inbox.isLoading ? <SkeletonRows /> : inbox.isError ? (
        <div className="p-6"><EmptyState title="Impossibile caricare le revisioni" action={<Button onClick={() => inbox.refetch()}>Riprova</Button>} /></div>
      ) : inbox.data?.items.length === 0 ? (
        <div className="p-6"><EmptyState title={activeFilterCount || search.has('q') ? 'Nessuna revisione corrisponde ai filtri' : 'Non ci sono revisioni da controllare'} description={activeFilterCount || search.has('q') ? 'Modifica o cancella i filtri per vedere altre revisioni.' : 'Avvia una scansione o cerca corrispondenze da un file.'} action={(activeFilterCount || search.has('q')) ? <Button onClick={clearFilters}>Cancella filtri</Button> : undefined} /></div>
      ) : inbox.data ? (
        <div className="divide-y divide-border-subtle">
          <div className="px-4 py-3 text-sm text-text-secondary sm:px-5">{inbox.data.total} revisioni nell’ordine di priorità</div>
          {inbox.data.items.map((review) => (
            <article id={`review-${review.id}`} key={review.id} className="flex gap-3 p-4 sm:items-center sm:gap-4 sm:px-5">
              <ThumbnailTile src={review.cover_thumbnail_url ?? undefined} label={review.cover_thumbnail_url ? 'Cover proposta' : 'Nessuna cover proposta'} />
              <button
                type="button"
                onClick={() => open(review.id)}
                className="focus-ring min-w-0 flex-1 rounded-md p-1 text-left"
                aria-label={`Apri revisione: ${review.filename ?? review.title}`}
              >
                <div className="break-words font-medium text-text-primary">{review.filename ?? review.title}</div>
                <div className="mt-1 break-all font-mono text-2xs text-text-secondary">{review.path ?? 'Percorso sorgente non disponibile'}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge tone={toneForState(review.state)}>{STATE_LABEL[review.state]}</Badge>
                  <Badge tone="neutral">{review.confidence === null ? review.confidence_label : `${Math.round(review.confidence * 100)}%`}</Badge>
                  {review.candidate_source && <Badge tone="neutral">{review.candidate_source}</Badge>}
                  {review.issues.map((item, index) => <Badge key={`${item.kind}-${index}`} tone="conflict">Problema: {item.message}</Badge>)}
                </div>
              </button>
              <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
                <Button size="sm" variant="secondary" onClick={() => open(review.id)}>Apri</Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={reject.isPending || review.pending_operations === 0}
                  onClick={() => reject.mutate(review.id)}
                >
                  Rifiuta proposte
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </div>
  )
}
