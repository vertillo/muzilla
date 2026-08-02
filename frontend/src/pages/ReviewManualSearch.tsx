import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { Badge, Button, EmptyState } from '@/components/ui'
import {
  getManualCandidateSearchCapabilities,
  getReviewBundle,
  importManualCandidate,
  searchManualCandidates,
  type ManualCandidateSearchParams,
} from '@/lib/api'
import type { ManualCandidateSearchResult } from '@/lib/types'

type SearchForm = Pick<
  ManualCandidateSearchParams,
  'title' | 'artist' | 'album' | 'year' | 'duration_ms' | 'isrc'
>

const INITIAL_FORM: SearchForm = {
  title: '',
  artist: '',
  album: '',
  year: undefined,
  duration_ms: undefined,
  isrc: '',
}

function providerLabel(provider: string): string {
  return provider === 'musicbrainz' ? 'MusicBrainz' : provider === 'deezer' ? 'Deezer' : 'Discogs'
}

function outcomeLabel(status: string): string {
  if (status === 'zero_results') return 'No results'
  if (status === 'not_configured') return 'Not configured'
  if (status === 'failed') return 'Temporarily unavailable'
  return 'Results found'
}

function candidateKey(source: string, refId: string): string {
  return `${source}:${refId}`
}

export function ReviewManualSearch() {
  const { id } = useParams<{ id: string }>()
  const reviewId = id ? Number(id) : NaN
  const queryClient = useQueryClient()
  const [form, setForm] = useState<SearchForm>(INITIAL_FORM)
  const [selectedProviders, setSelectedProviders] = useState<string[]>([])
  const [providerSelectionInitialized, setProviderSelectionInitialized] = useState(false)
  const [result, setResult] = useState<ManualCandidateSearchResult | null>(null)

  const review = useQuery({
    queryKey: ['review', reviewId],
    queryFn: () => getReviewBundle(reviewId),
    enabled: Number.isFinite(reviewId),
  })
  const capabilities = useQuery({
    queryKey: ['review-search-capabilities', reviewId],
    queryFn: () => getManualCandidateSearchCapabilities(reviewId),
    enabled: Number.isFinite(reviewId),
  })

  const availableProviders = useMemo(
    () => capabilities.data?.filter((capability) => capability.status === 'available') ?? [],
    [capabilities.data],
  )
  useEffect(() => {
    if (!providerSelectionInitialized && availableProviders.length > 0) {
      setSelectedProviders(availableProviders.map((capability) => capability.provider))
      setProviderSelectionInitialized(true)
    }
  }, [availableProviders, providerSelectionInitialized])
  const providers = selectedProviders

  const search = useMutation({
    mutationFn: (page: number) => searchManualCandidates(reviewId, {
      ...form,
      providers,
      page,
      page_size: 10,
    }),
    onSuccess: (next, page) => {
      setResult((previous) => page > 0 && previous
        ? { ...next, candidates: [...previous.candidates, ...next.candidates] }
        : next)
    },
  })
  const selectCandidate = useMutation({
    mutationFn: ({ source, refId }: { source: string; refId: string }) =>
      importManualCandidate(reviewId, source, refId),
    onSuccess: (updated) => {
      queryClient.setQueryData(['review', reviewId], updated)
      setResult((previous) => previous)
    },
  })

  if (!Number.isFinite(reviewId)) {
    return <EmptyState title="Review not found" description="A numeric review ID is required." />
  }
  if (review.isLoading || capabilities.isLoading) {
    return <EmptyState title="Loading review…" description="Preparing manual search." />
  }
  if (review.isError || capabilities.isError || !review.data) {
    return <EmptyState title="Review unavailable" description="This review cannot be searched right now." />
  }

  const currentCandidate = review.data.current_revision
  const capabilityRows = capabilities.data ?? []
  const updateText = (field: 'title' | 'artist' | 'album' | 'isrc', value: string) => {
    setForm((previous) => ({ ...previous, [field]: value }))
  }
  const updateNumber = (field: 'year' | 'duration_ms', value: string) => {
    const parsed = Number(value)
    setForm((previous) => ({ ...previous, [field]: value ? parsed : undefined }))
  }
  const toggleProvider = (provider: string) => {
    setSelectedProviders((previous) =>
      previous.includes(provider) ? previous.filter((item) => item !== provider) : [...previous, provider],
    )
  }
  const submit = (page = 0) => {
    search.mutate(page)
  }

  return (
    <main className="max-w-5xl mx-auto px-5 py-6">
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <p className="font-mono text-2xs uppercase tracking-wide text-text-muted">Review #{reviewId}</p>
          <h1 className="text-xl font-semibold text-text-primary">Find a candidate</h1>
          <p className="text-sm text-text-secondary mt-1">Searches add a candidate to this review; they never change music files.</p>
        </div>
        {currentCandidate.candidate_source && (
          <Badge tone="added">Selected: {providerLabel(currentCandidate.candidate_source)}</Badge>
        )}
      </div>

      <form
        className="rounded-md border border-border-subtle bg-bg-surface p-4"
        onSubmit={(event) => { event.preventDefault(); submit() }}
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-sm text-text-secondary">Title
            <input aria-label="Title" value={form.title ?? ''} onChange={(event) => updateText('title', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
          <label className="text-sm text-text-secondary">Artist
            <input aria-label="Artist" value={form.artist ?? ''} onChange={(event) => updateText('artist', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
          <label className="text-sm text-text-secondary">Album / release
            <input aria-label="Album / release" value={form.album ?? ''} onChange={(event) => updateText('album', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
          <label className="text-sm text-text-secondary">Year (optional)
            <input aria-label="Year" inputMode="numeric" value={form.year ?? ''} onChange={(event) => updateNumber('year', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
          <label className="text-sm text-text-secondary">Duration ms (optional)
            <input aria-label="Duration ms" inputMode="numeric" value={form.duration_ms ?? ''} onChange={(event) => updateNumber('duration_ms', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
          <label className="text-sm text-text-secondary">ISRC (optional)
            <input aria-label="ISRC" value={form.isrc ?? ''} onChange={(event) => updateText('isrc', event.target.value)} className="mt-1 w-full rounded border border-border-subtle bg-bg-base p-2 text-text-primary" />
          </label>
        </div>
        <fieldset className="mt-4">
          <legend className="text-sm text-text-secondary">Providers</legend>
          <div className="flex flex-wrap gap-3 mt-2">
            {capabilityRows.map((capability) => (
              <label key={capability.provider} className="flex items-center gap-2 text-sm text-text-primary">
                <input
                  type="checkbox"
                  checked={providers.includes(capability.provider)}
                  disabled={capability.status !== 'available'}
                  onChange={() => toggleProvider(capability.provider)}
                />
                {providerLabel(capability.provider)} <span className="text-text-muted">({capability.status === 'available' ? 'ready' : 'not configured'})</span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="mt-4 flex items-center gap-3">
          <Button type="submit" disabled={search.isPending || providers.length === 0}>
            {search.isPending ? 'Searching…' : 'Search'}
          </Button>
          {search.isError && <span className="text-sm text-diff-removed">{search.error.message}</span>}
        </div>
      </form>

      {result && (
        <section className="mt-6" aria-live="polite">
          <div className="flex flex-wrap gap-2 mb-4">
            {result.provider_outcomes.map((outcome) => (
              <Badge key={outcome.provider} tone={outcome.status === 'failed' ? 'removed' : outcome.status === 'results' ? 'added' : 'neutral'}>
                {providerLabel(outcome.provider)}: {outcomeLabel(outcome.status)}
              </Badge>
            ))}
          </div>
          {result.candidates.length === 0 ? (
            <EmptyState title="No candidates" description="Try a different query, provider, or a supported URL when that option is available." />
          ) : (
            <div className="flex flex-col gap-3">
              {result.candidates.map((candidate) => {
                const selected = candidate.source === currentCandidate.candidate_source && candidate.ref_id === currentCandidate.candidate_ref
                const key = candidateKey(candidate.source, candidate.ref_id)
                return (
                  <article key={key} className="rounded-md border border-border-subtle bg-bg-surface p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium text-text-primary">{candidate.representative_title ?? candidate.album ?? 'Untitled'}</p>
                        <p className="text-sm text-text-secondary">{[candidate.representative_artist ?? candidate.album_artist, candidate.album, candidate.year].filter(Boolean).join(' · ')}</p>
                        <p className="text-2xs text-text-muted mt-1">{providerLabel(candidate.source)} · {candidate.track_count ?? 'Unknown'} tracks</p>
                      </div>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={selected || selectCandidate.isPending}
                        onClick={() => selectCandidate.mutate({ source: candidate.source, refId: candidate.ref_id })}
                      >
                        {selected ? 'Selected' : selectCandidate.isPending ? 'Adding…' : 'Use this result'}
                      </Button>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
          {result.has_more && (
            <div className="mt-4">
              <Button variant="secondary" disabled={search.isPending} onClick={() => submit(result.query.page + 1)}>
                Show more
              </Button>
            </div>
          )}
        </section>
      )}
    </main>
  )
}
