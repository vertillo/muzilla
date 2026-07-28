import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '@/lib/api'
import { Badge, Button, Checkbox, EmptyState, Input, Select, SkeletonRows, TableRow } from '@/components/ui'
import { useTracks } from '@/hooks/useTracks'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetKey, type FacetState } from '@/hooks/useTrackFacets'
import { useCatalogSelectionStore } from '@/store/catalogSelection'
import type { SortKey, TrackSummary } from '@/lib/types'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'title', label: 'Title' },
  { value: 'artist', label: 'Artist' },
  { value: 'album', label: 'Album' },
  { value: 'added', label: 'Date added' },
]

const FLAG_OPTIONS: { value: FacetKey; label: string }[] = [
  { value: 'missing-art', label: 'Missing art' },
  { value: 'unmatched', label: 'No album tag' },
  { value: 'errored', label: 'Probe errors' },
]

function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function Catalog() {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  const [facets, setFacets] = useState<FacetState>(EMPTY_FACETS)
  const selected = useCatalogSelectionStore((s) => s.selected)
  const toggleSelected = useCatalogSelectionStore((s) => s.toggle)
  const clearSelected = useCatalogSelectionStore((s) => s.clear)
  const navigate = useNavigate()

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, error, refetch } =
    useTracks(search, sort, facets)

  const allTracks = useMemo<TrackSummary[]>(() => data?.pages.flatMap((p) => p.items) ?? [], [data])
  // total now comes from the server, computed over the current
  // search+filter context against the full table — not a client-side
  // count of whatever pages happen to be loaded (docs/PHASE8_BRIEF.md
  // Phase 7 suggestion #1). Filters are applied server-side too (see
  // useTracks), so visibleTracks re-filtering the loaded rows is now
  // redundant-but-harmless rather than the source of truth.
  const total = data?.pages[0]?.total ?? 0
  const facetOptions = useFacetOptions(search)
  const visibleTracks = useMemo(() => applyFacets(allTracks, facets), [allTracks, facets])
  const hasActiveSearchOrFilter =
    search.trim() !== '' ||
    facets.artist !== null ||
    facets.album !== null ||
    facets.genre !== null ||
    facets.format !== null ||
    facets.flags.size > 0

  const parentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: visibleTracks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 32,
    overscan: 12,
  })

  function toggleFlag(flag: FacetKey) {
    setFacets((prev) => {
      const next = new Set(prev.flags)
      if (next.has(flag)) next.delete(flag)
      else next.add(flag)
      return { ...prev, flags: next }
    })
  }

  const items = rowVirtualizer.getVirtualItems()
  const lastItem = items[items.length - 1]
  if (lastItem && lastItem.index >= visibleTracks.length - 20 && hasNextPage && !isFetchingNextPage) {
    void fetchNextPage()
  }

  return (
    <div className="flex h-full font-sans text-text-primary bg-canvas">
      <aside className="w-[220px] shrink-0 border-r border-border-subtle p-5 flex flex-col gap-6 overflow-y-auto">
        <Facet
          label="Artist"
          value={facets.artist}
          options={facetOptions.artists}
          onChange={(v) => setFacets((f) => ({ ...f, artist: v }))}
        />
        <Facet
          label="Album"
          value={facets.album}
          options={facetOptions.albums}
          onChange={(v) => setFacets((f) => ({ ...f, album: v }))}
        />
        <Facet
          label="Genre"
          value={facets.genre}
          options={facetOptions.genres}
          onChange={(v) => setFacets((f) => ({ ...f, genre: v }))}
        />
        <Facet
          label="Format"
          value={facets.format}
          options={facetOptions.formats}
          onChange={(v) => setFacets((f) => ({ ...f, format: v }))}
        />

        <div className="flex flex-col gap-3">
          <FacetHeading>Flags</FacetHeading>
          {FLAG_OPTIONS.map((opt) => (
            <Checkbox
              key={opt.value}
              label={opt.label}
              checked={facets.flags.has(opt.value)}
              onChange={() => toggleFlag(opt.value)}
            />
          ))}
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-4 py-4 px-5 border-b border-border-subtle">
          <div className="w-[320px]">
            <Input value={search} onChange={setSearch} placeholder="Search title, artist, album…" />
          </div>
          <div className="w-[160px]">
            <Select
              value={sort}
              options={SORT_OPTIONS}
              onChange={(v) => setSort(v as SortKey)}
            />
          </div>
          {selected.size > 0 && (
            <>
              <span className="text-xs text-text-secondary">{selected.size} selected</span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigate(`/edit?ids=${[...selected].join(',')}`)}
              >
                Bulk edit
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => navigate(`/rename?ids=${[...selected].join(',')}`)}
              >
                Rename
              </Button>
              <Button variant="ghost" size="sm" onClick={clearSelected}>
                Clear selection
              </Button>
            </>
          )}
          <div className="ml-auto text-xs text-text-muted">
            {/* visibleTracks.length is now "matching rows loaded so far"
                (the underlying query already applies every active facet
                server-side — see useTracks), and total is the server-side
                count of ALL matches for the current search+filters, not a
                client-side count of loaded pages. So this reads correctly
                as "loaded so far of total matches", and stays honest once
                the virtualizer has fetched every page too, since the two
                converge. */}
            {visibleTracks.length} of {total} tracks
          </div>
        </div>

        <div className="flex px-3 h-[var(--row-height-compact)] items-center border-b border-border-subtle text-2xs text-text-muted font-mono uppercase tracking-wide gap-4">
          <div className="w-6" />
          <div className="w-9" />
          <div className="flex-[2_1_0%] min-w-0">Title</div>
          <div className="flex-[1.5_1_0%] min-w-0">Artist</div>
          <div className="flex-[1.5_1_0%] min-w-0">Album</div>
          <div className="w-[60px]">Year</div>
          <div className="w-[60px]">Fmt</div>
          <div className="w-[60px] text-right">Time</div>
        </div>

        {isError ? (
          <div className="p-9">
            <EmptyState
              title="Couldn't load tracks"
              description={error instanceof ApiError ? error.message : 'The server returned an error.'}
              action={<Button onClick={() => refetch()}>Retry</Button>}
            />
          </div>
        ) : isLoading ? (
          <SkeletonRows />
        ) : visibleTracks.length === 0 ? (
          <div className="p-9">
            <EmptyState
              title="No tracks found"
              description={
                // total is the *search-scoped* count (db/repo/tracks.py's
                // list_tracks), not the library's overall size — using it
                // to decide "is the library empty" told users to run a
                // scan even when their search/filter simply matched
                // nothing (docs/KNOWN_BUGS.md #1). Only show that message
                // when nothing is actively filtering the view.
                !hasActiveSearchOrFilter
                  ? 'Run `muzilla scan <path>` to index your library.'
                  : 'No tracks match the current search and filters.'
              }
            />
          </div>
        ) : (
          <div ref={parentRef} className="flex-1 overflow-y-auto">
            <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
              {items.map((virtualRow) => {
                const track = visibleTracks[virtualRow.index]
                return (
                  <div
                    key={track.id}
                    className="absolute top-0 left-0 w-full"
                    style={{ transform: `translateY(${virtualRow.start}px)` }}
                  >
                    <TrackRow track={track} selected={selected.has(track.id)} onToggleSelected={toggleSelected} />
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

function TrackRow({
  track,
  selected,
  onToggleSelected,
}: {
  track: TrackSummary
  selected: boolean
  onToggleSelected: (trackId: number) => void
}) {
  return (
    <TableRow state={selected ? 'selected' : 'default'}>
      <div
        data-testid="catalog-row-checkbox"
        className="w-6 flex justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        <Checkbox checked={selected} onChange={() => onToggleSelected(track.id)} />
      </div>
      <div className="w-9 flex gap-2 justify-center">
        {track.probe_error ? (
          // docs/PLAN.md §12e step 6.5 item 6: icon-only badges need an
          // aria-label, not just a hover-only title, so a screen reader
          // (or anyone not hovering) knows what the dot means — never
          // rely on hue alone (§9's own design-system contract).
          <span role="img" aria-label={`Probe error: ${track.probe_error}`} title={track.probe_error}>
            <Badge tone="conflict" dot />
          </span>
        ) : (
          <>
            {!track.has_embedded_art && (
              <span role="img" aria-label="No embedded art" title="No embedded art">
                <Badge tone="neutral" dot />
              </span>
            )}
            {!track.has_lyrics && (
              <span role="img" aria-label="No lyrics" title="No lyrics">
                <Badge tone="neutral" dot />
              </span>
            )}
          </>
        )}
      </div>
      <Link
        to={`/edit?ids=${track.id}`}
        className="flex-[2_1_0%] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-inherit no-underline"
      >
        {track.title ?? track.filename}
      </Link>
      <div className="flex-[1.5_1_0%] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-secondary">
        {track.artist ?? '—'}
      </div>
      <div className="flex-[1.5_1_0%] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-secondary">
        {track.album ?? '—'}
      </div>
      <div className="w-[60px] text-text-secondary">{track.year ?? '—'}</div>
      <div className="w-[60px] text-text-secondary">{track.format ?? '—'}</div>
      <div className="w-[60px] text-right text-text-secondary font-mono">
        {formatDuration(track.duration_ms)}
      </div>
    </TableRow>
  )
}

function FacetHeading({ children }: { children: string }) {
  return (
    <div className="text-2xs text-text-muted font-mono uppercase tracking-wide">{children}</div>
  )
}

function Facet({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string | null
  options: string[]
  onChange: (value: string | null) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <FacetHeading>{label}</FacetHeading>
      <Select
        value={value ?? ''}
        options={[{ value: '', label: 'All' }, ...options.map((o) => ({ value: o, label: o }))]}
        onChange={(v) => onChange(v === '' ? null : v)}
      />
    </div>
  )
}
