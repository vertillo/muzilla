import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Checkbox, EmptyState, Input, Select, TableRow } from '@/components/ui'
import { useTracks } from '@/hooks/useTracks'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetKey, type FacetState } from '@/hooks/useTrackFacets'
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
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const navigate = useNavigate()

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useTracks(search, sort)

  const allTracks = useMemo<TrackSummary[]>(() => data?.pages.flatMap((p) => p.items) ?? [], [data])
  const total = data?.pages[0]?.total ?? 0
  const facetOptions = useFacetOptions(allTracks)
  const visibleTracks = useMemo(() => applyFacets(allTracks, facets), [allTracks, facets])

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

  function toggleSelected(trackId: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
  }

  const items = rowVirtualizer.getVirtualItems()
  const lastItem = items[items.length - 1]
  if (lastItem && lastItem.index >= visibleTracks.length - 20 && hasNextPage && !isFetchingNextPage) {
    void fetchNextPage()
  }

  return (
    <div
      style={{
        display: 'flex',
        height: '100%',
        fontFamily: 'var(--font-sans)',
        color: 'var(--text-primary)',
        background: 'var(--bg-canvas)',
      }}
    >
      <aside
        style={{
          width: 220,
          flexShrink: 0,
          borderRight: '1px solid var(--border-subtle)',
          padding: 'var(--space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-6)',
          overflowY: 'auto',
        }}
      >
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

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
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

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-4)',
            padding: 'var(--space-4) var(--space-5)',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div style={{ width: 320 }}>
            <Input value={search} onChange={setSearch} placeholder="Search title, artist, album…" />
          </div>
          <div style={{ width: 160 }}>
            <Select
              value={sort}
              options={SORT_OPTIONS}
              onChange={(v) => setSort(v as SortKey)}
            />
          </div>
          {selected.size > 0 && (
            <>
              <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-secondary)' }}>
                {selected.size} selected
              </span>
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
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                Clear selection
              </Button>
            </>
          )}
          <div style={{ marginLeft: 'auto', fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
            {visibleTracks.length} of {total} tracks
          </div>
        </div>

        <div
          style={{
            display: 'flex',
            padding: '0 var(--space-3)',
            height: 28,
            alignItems: 'center',
            borderBottom: '1px solid var(--border-subtle)',
            fontSize: 'var(--text-2xs-size)',
            color: 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--tracking-wide)',
            gap: 'var(--space-4)',
          }}
        >
          <div style={{ width: 20 }} />
          <div style={{ width: 40 }} />
          <div style={{ flex: '2 1 0', minWidth: 0 }}>Title</div>
          <div style={{ flex: '1.5 1 0', minWidth: 0 }}>Artist</div>
          <div style={{ flex: '1.5 1 0', minWidth: 0 }}>Album</div>
          <div style={{ width: 60 }}>Year</div>
          <div style={{ width: 60 }}>Fmt</div>
          <div style={{ width: 60, textAlign: 'right' }}>Time</div>
        </div>

        {isLoading ? (
          <div style={{ padding: 'var(--space-9)' }}>
            <EmptyState title="Loading tracks…" />
          </div>
        ) : visibleTracks.length === 0 ? (
          <div style={{ padding: 'var(--space-9)' }}>
            <EmptyState
              title="No tracks found"
              description={
                total === 0
                  ? 'Run `muzilla scan <path>` to index your library.'
                  : 'No tracks match the current search and filters.'
              }
            />
          </div>
        ) : (
          <div ref={parentRef} style={{ flex: 1, overflowY: 'auto' }}>
            <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
              {items.map((virtualRow) => {
                const track = visibleTracks[virtualRow.index]
                return (
                  <div
                    key={track.id}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
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
      <div style={{ width: 20, display: 'flex', justifyContent: 'center' }} onClick={(e) => e.stopPropagation()}>
        <Checkbox checked={selected} onChange={() => onToggleSelected(track.id)} />
      </div>
      <div style={{ width: 40, display: 'flex', gap: 4, justifyContent: 'center' }}>
        {track.probe_error ? (
          <Badge tone="conflict" dot />
        ) : (
          <>
            {!track.has_embedded_art && (
              <span title="No embedded art">
                <Badge tone="neutral" dot />
              </span>
            )}
            {!track.has_lyrics && (
              <span title="No lyrics">
                <Badge tone="neutral" dot />
              </span>
            )}
          </>
        )}
      </div>
      <Link
        to={`/edit?ids=${track.id}`}
        style={{
          flex: '2 1 0',
          minWidth: 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          color: 'inherit',
          textDecoration: 'none',
        }}
      >
        {track.title ?? track.filename}
      </Link>
      <div style={{ flex: '1.5 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
        {track.artist ?? '—'}
      </div>
      <div style={{ flex: '1.5 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
        {track.album ?? '—'}
      </div>
      <div style={{ width: 60, color: 'var(--text-secondary)' }}>{track.year ?? '—'}</div>
      <div style={{ width: 60, color: 'var(--text-secondary)' }}>{track.format ?? '—'}</div>
      <div style={{ width: 60, textAlign: 'right', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
        {formatDuration(track.duration_ms)}
      </div>
    </TableRow>
  )
}

function FacetHeading({ children }: { children: string }) {
  return (
    <div
      style={{
        fontSize: 'var(--text-2xs-size)',
        color: 'var(--text-muted)',
        fontFamily: 'var(--font-mono)',
        textTransform: 'uppercase',
        letterSpacing: 'var(--tracking-wide)',
      }}
    >
      {children}
    </div>
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <FacetHeading>{label}</FacetHeading>
      <Select
        value={value ?? ''}
        options={[{ value: '', label: 'All' }, ...options.map((o) => ({ value: o, label: o }))]}
        onChange={(v) => onChange(v === '' ? null : v)}
      />
    </div>
  )
}
