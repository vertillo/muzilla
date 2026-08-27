import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError } from '@/lib/api'
import { Badge, Button, EmptyState, Input, Select, SkeletonRows } from '@/components/ui'
import { useTracks } from '@/hooks/useTracks'
import { useDetectDuplicates, useDismissDuplicate, useDuplicateGroups } from '@/hooks/useDuplicates'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetKey, type FacetState } from '@/hooks/useTrackFacets'
import type { SortKey, TrackSummary } from '@/lib/types'

type CatalogColumnKey = 'title' | 'artist' | 'album' | 'added' | 'format' | 'duration' | 'status'

interface CatalogColumnDefinition {
  key: CatalogColumnKey
  label: string
  defaultWidth: number
  minWidth: number
  maxWidth: number
  sortKey?: SortKey
}

const CATALOG_COLUMNS: readonly CatalogColumnDefinition[] = [
  { key: 'title', label: 'Titolo', defaultWidth: 240, minWidth: 160, maxWidth: 420, sortKey: 'title' },
  { key: 'artist', label: 'Artista', defaultWidth: 180, minWidth: 120, maxWidth: 320, sortKey: 'artist' },
  { key: 'album', label: 'Album', defaultWidth: 220, minWidth: 140, maxWidth: 360, sortKey: 'album' },
  { key: 'added', label: 'Aggiunto', defaultWidth: 120, minWidth: 96, maxWidth: 200, sortKey: 'added' },
  { key: 'format', label: 'Formato', defaultWidth: 110, minWidth: 88, maxWidth: 180 },
  { key: 'duration', label: 'Durata', defaultWidth: 110, minWidth: 88, maxWidth: 180 },
  { key: 'status', label: 'Stato', defaultWidth: 220, minWidth: 188, maxWidth: 360 },
]

type CatalogColumnWidths = Record<CatalogColumnKey, number>

const CATALOG_COLUMN_WIDTHS_STORAGE_KEY = 'muzilla.catalog.column-widths'

function defaultCatalogColumnWidths(): CatalogColumnWidths {
  return Object.fromEntries(CATALOG_COLUMNS.map(({ key, defaultWidth }) => [key, defaultWidth])) as CatalogColumnWidths
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function catalogStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function clampCatalogColumnWidth(columnKey: CatalogColumnKey, width: number): number {
  const column = CATALOG_COLUMNS.find(({ key }) => key === columnKey)
  if (!column || !Number.isFinite(width)) return column?.defaultWidth ?? 0
  return Math.min(column.maxWidth, Math.max(column.minWidth, Math.round(width)))
}

function readCatalogColumnWidths(storage: Storage | null = catalogStorage()): CatalogColumnWidths {
  const widths = defaultCatalogColumnWidths()
  if (!storage) return widths

  try {
    const raw = storage.getItem(CATALOG_COLUMN_WIDTHS_STORAGE_KEY)
    if (!raw) return widths
    const parsed: unknown = JSON.parse(raw)
    if (!isRecord(parsed)) return widths
    for (const column of CATALOG_COLUMNS) {
      const saved = parsed[column.key]
      if (typeof saved === 'number' && Number.isFinite(saved)) {
        widths[column.key] = clampCatalogColumnWidth(column.key, saved)
      }
    }
  } catch {
    // Private browsing, blocked storage, and malformed preferences use defaults.
  }
  return widths
}

function persistCatalogColumnWidths(widths: CatalogColumnWidths): void {
  try {
    catalogStorage()?.setItem(CATALOG_COLUMN_WIDTHS_STORAGE_KEY, JSON.stringify(widths))
  } catch {
    // Column layout is a preference; a storage failure must not affect the catalog.
  }
}

function clearCatalogColumnWidths(): void {
  try {
    catalogStorage()?.removeItem(CATALOG_COLUMN_WIDTHS_STORAGE_KEY)
  } catch {
    // Reset still applies in memory when storage is unavailable.
  }
}

function useCatalogColumnWidths() {
  const [widths, setWidths] = useState<CatalogColumnWidths>(() => readCatalogColumnWidths())
  const widthsRef = useRef(widths)

  const resizeColumn = useCallback((columnKey: CatalogColumnKey, width: number) => {
    const next = { ...widthsRef.current, [columnKey]: clampCatalogColumnWidth(columnKey, width) }
    widthsRef.current = next
    setWidths(next)
    persistCatalogColumnWidths(next)
  }, [])

  const resetColumnWidths = useCallback(() => {
    const defaults = defaultCatalogColumnWidths()
    widthsRef.current = defaults
    setWidths(defaults)
    clearCatalogColumnWidths()
  }, [])

  return { widths, resizeColumn, resetColumnWidths }
}

const FLAG_OPTIONS: { value: FacetKey; label: string }[] = [
  { value: 'missing-art', label: 'Senza cover' },
  { value: 'unmatched', label: 'Da identificare' },
  { value: 'errored', label: 'Errori di lettura' },
]

function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  const seconds = Math.round(ms / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function trackStatuses(track: TrackSummary): string[] {
  if (track.missing_since) return ['File mancante']
  if (track.probe_error) return ['Errore di lettura']
  const statuses = []
  if (!track.has_embedded_art) statuses.push('Senza cover')
  if (!track.has_lyrics) statuses.push('Senza testo')
  if (!track.album) statuses.push('Da identificare')
  return statuses.length ? statuses : ['Pronto']
}

export function Catalog() {
  const [params] = useSearchParams()
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')
  const [facets, setFacets] = useState<FacetState>(EMPTY_FACETS)
  const dashboardStatus = params.get('status')
  const duplicatesTool = params.get('tool') === 'duplicates'
  const duplicates = useDuplicateGroups()
  const detectDuplicates = useDetectDuplicates()
  const dismissDuplicate = useDismissDuplicate()
  const effectiveFacets = useMemo(() => {
    if (dashboardStatus === 'missing') return { ...facets, flags: new Set([...facets.flags, 'missing' as FacetKey]) }
    if (dashboardStatus === 'errored') return { ...facets, flags: new Set<FacetKey>([...facets.flags, 'errored']) }
    return facets
  }, [dashboardStatus, facets])
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, error, refetch } = useTracks(search, sort, sortDirection, effectiveFacets)
  const tracks = useMemo(() => applyFacets(data?.pages.flatMap((page) => page.items) ?? [], facets), [data, facets])
  const total = data?.pages[0]?.total ?? 0
  const options = useFacetOptions(search)
  const filtered = search.trim() || facets.artist || facets.album || facets.genre || facets.format || facets.flags.size

  const setFlag = (flag: FacetKey) => setFacets((current) => {
    const flags = new Set(current.flags)
    if (flags.has(flag)) flags.delete(flag)
    else flags.add(flag)
    return { ...current, flags }
  })
  const setSortFromHeader = (key: SortKey) => {
    if (key === sort) setSortDirection((direction) => direction === 'asc' ? 'desc' : 'asc')
    else {
      setSort(key)
      setSortDirection('asc')
    }
  }

  return (
    <div className="min-h-0 bg-canvas">
      <header className="border-b border-border-subtle p-4 sm:p-5">
        <h1 className="text-xl font-semibold">Catalogo</h1>
        <p className="mt-1 text-sm text-text-secondary">Cerca e controlla i file indicizzati. Ogni modifica passa da una revisione.</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <div className="min-w-[min(100%,22rem)] flex-1"><Input value={search} onChange={setSearch} placeholder="Cerca titolo, artista, album o percorso…" /></div>
          <Select value={facets.artist ?? ''} options={[{ value: '', label: 'Tutti gli artisti' }, ...options.artists.map((item) => ({ value: item, label: item }))]} onChange={(artist) => setFacets((value) => ({ ...value, artist: artist || null }))} />
          <Select value={facets.format ?? ''} options={[{ value: '', label: 'Tutti i formati' }, ...options.formats.map((item) => ({ value: item, label: item }))]} onChange={(format) => setFacets((value) => ({ ...value, format: format || null }))} />
        </div>
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Filtri catalogo">
          {FLAG_OPTIONS.map((option) => <Button key={option.value} size="sm" variant={facets.flags.has(option.value) ? 'secondary' : 'ghost'} onClick={() => setFlag(option.value)}>{option.label}</Button>)}
          {filtered ? <Button size="sm" variant="ghost" onClick={() => { setSearch(''); setFacets(EMPTY_FACETS) }}>Cancella filtri</Button> : null}
          <Link className="focus-ring self-center rounded px-2 text-xs text-accent-text" to="/catalog?tool=duplicates">Possibili duplicati</Link>
          <span className="ml-auto self-center text-xs text-text-muted">{tracks.length} di {total} file</span>
        </div>
      </header>

      {duplicatesTool ? <section className="p-4 sm:p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">Possibili duplicati</h2><p className="mt-1 max-w-2xl text-sm text-text-secondary">L’evidenza confronta impronta AcoustID, recording ID MusicBrainz, durata e qualità del file. È una segnalazione: Muzilla non elimina nulla automaticamente.</p></div><Button disabled={detectDuplicates.isPending} onClick={() => detectDuplicates.mutate()}>{detectDuplicates.isPending ? 'Analisi in coda…' : 'Analizza duplicati'}</Button></div>{duplicates.isLoading ? <SkeletonRows /> : duplicates.data?.items.length ? <div className="mt-4 space-y-3">{duplicates.data.items.map((group) => <article key={group.id} className="rounded-md border border-border-subtle p-4"><h3 className="font-medium">Stesso recording verificato</h3><p className="mt-1 text-sm text-text-secondary">Evidenza: {group.basis === 'acoustid' ? 'impronta AcoustID → recording ID MusicBrainz' : group.basis} · recording {group.mb_recording_id}</p><ul className="mt-3 space-y-2">{group.tracks.map((item) => <li key={item.id}><Link className="focus-ring rounded text-sm text-inherit" to={`/catalog/${item.id}`}>{item.title ?? item.path}</Link><span className="ml-2 text-xs text-text-muted">{item.format ?? 'formato sconosciuto'}{item.bitrate ? ` · ${item.bitrate} kbps` : ''}{item.duration_ms ? ` · ${formatDuration(item.duration_ms)}` : ''}</span></li>)}</ul><div className="mt-3"><Button size="sm" variant="ghost" disabled={dismissDuplicate.isPending} onClick={() => dismissDuplicate.mutate(group.id)}>Ignora segnalazione</Button></div></article>)}</div> : <EmptyState title="Nessun possibile duplicato" description="Avvia l’analisi dopo avere calcolato le impronte dei file." />}</section>
        : isError ? <div className="p-6"><EmptyState title="Impossibile caricare il catalogo" description={error instanceof ApiError ? error.message : 'Il server ha restituito un errore.'} action={<Button onClick={() => refetch()}>Riprova</Button>} /></div>
        : isLoading ? <SkeletonRows />
          : tracks.length === 0 ? <div className="p-6"><EmptyState title="Nessun file trovato" description={filtered ? 'Nessun file corrisponde a ricerca e filtri correnti.' : 'Avvia una scansione per indicizzare la libreria.'} /></div>
            : <>
              <CatalogTable tracks={tracks} sort={sort} sortDirection={sortDirection} onSort={setSortFromHeader} />
              {hasNextPage ? <div className="p-4 text-center"><Button variant="secondary" disabled={isFetchingNextPage} onClick={() => fetchNextPage()}>{isFetchingNextPage ? 'Caricamento…' : 'Carica altri file'}</Button></div> : null}
            </>}
    </div>
  )
}

interface CatalogTableProps {
  tracks: TrackSummary[]
  sort: SortKey
  sortDirection: 'asc' | 'desc'
  onSort: (key: SortKey) => void
}

interface ColumnResizeHandleProps {
  column: CatalogColumnDefinition
  width: number
  onResize: (columnKey: CatalogColumnKey, width: number) => void
}

const KEYBOARD_RESIZE_STEP = 16

function ColumnResizeHandle({ column, width, onResize }: ColumnResizeHandleProps) {
  const pointer = useRef<{ id: number; startX: number; startWidth: number } | null>(null)

  const onPointerDown = (event: PointerEvent<HTMLSpanElement>) => {
    event.preventDefault()
    event.stopPropagation()
    pointer.current = { id: event.pointerId, startX: event.clientX, startWidth: width }
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // Synthetic events and browsers without pointer capture still receive local moves.
    }
  }

  const onPointerMove = (event: PointerEvent<HTMLSpanElement>) => {
    const active = pointer.current
    if (!active || active.id !== event.pointerId) return
    event.preventDefault()
    onResize(column.key, active.startWidth + event.clientX - active.startX)
  }

  const onPointerEnd = (event: PointerEvent<HTMLSpanElement>) => {
    if (!pointer.current || pointer.current.id !== event.pointerId) return
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      // Pointer capture may not have been available for a synthetic event.
    }
    pointer.current = null
  }

  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    let nextWidth: number | null = null
    if (event.key === 'ArrowLeft') nextWidth = width - KEYBOARD_RESIZE_STEP
    if (event.key === 'ArrowRight') nextWidth = width + KEYBOARD_RESIZE_STEP
    if (event.key === 'Home') nextWidth = column.minWidth
    if (event.key === 'End') nextWidth = column.maxWidth
    if (nextWidth === null) return
    event.preventDefault()
    event.stopPropagation()
    onResize(column.key, nextWidth)
  }

  return (
    <span
      aria-label={`Ridimensiona colonna ${column.label}`}
      aria-orientation="vertical"
      aria-valuemax={column.maxWidth}
      aria-valuemin={column.minWidth}
      aria-valuenow={width}
      aria-valuetext={`${width} pixel di larghezza`}
      className="focus-ring absolute inset-y-0 right-0 z-10 w-7 cursor-col-resize touch-none rounded"
      data-column-key={column.key}
      onKeyDown={onKeyDown}
      onPointerCancel={onPointerEnd}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      role="separator"
      tabIndex={0}
      title="Trascina o usa le frecce per ridimensionare"
    >
      <span aria-hidden="true" className="absolute inset-y-2 left-1/2 w-px -translate-x-1/2 bg-border-default" />
    </span>
  )
}

export function CatalogTable({ tracks, sort, sortDirection, onSort }: CatalogTableProps) {
  const { widths, resizeColumn, resetColumnWidths } = useCatalogColumnWidths()
  const tableWidth = CATALOG_COLUMNS.reduce((total, column) => total + widths[column.key], 0)

  return (
    <section aria-label="Tabella catalogo">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-2 text-xs text-text-muted sm:px-5">
        <p id="catalog-column-help">Ridimensiona le colonne trascinando il divisore o usando le frecce della tastiera.</p>
        <Button size="sm" variant="ghost" onClick={resetColumnWidths}>Ripristina larghezze colonne</Button>
      </div>
      <div className="hidden overflow-x-auto md:block">
        <table
          aria-describedby="catalog-column-help"
          className="w-full min-w-[880px] table-fixed border-collapse text-left"
          style={{ minWidth: `${tableWidth}px`, width: `${tableWidth}px` }}
        >
          <caption className="sr-only">Catalogo dei file</caption>
          <colgroup>
            {CATALOG_COLUMNS.map((column) => (
              <col key={column.key} style={{ maxWidth: `${column.maxWidth}px`, minWidth: `${column.minWidth}px`, width: `${widths[column.key]}px` }} />
            ))}
          </colgroup>
          <thead className="border-b border-border-subtle text-xs text-text-muted">
            <tr>
              {CATALOG_COLUMNS.map((column) => {
                const sortKey = column.sortKey
                const isSorted = sortKey !== undefined && sort === sortKey
                return (
                  <th
                    key={column.key}
                    aria-sort={sortKey ? isSorted ? sortDirection === 'asc' ? 'ascending' : 'descending' : 'none' : undefined}
                    className="relative p-0 font-medium"
                    style={{ maxWidth: `${column.maxWidth}px`, minWidth: `${column.minWidth}px`, width: `${widths[column.key]}px` }}
                    scope="col"
                  >
                    <div className="flex min-w-0 items-center p-3 pr-7">
                      {sortKey ? <button type="button" className="focus-ring min-w-0 rounded p-1 text-left" onClick={() => onSort(sortKey)} aria-label={`Ordina per ${column.label}`}>
                        <span>{column.label}</span><span aria-hidden="true">{isSorted ? sortDirection === 'asc' ? ' ↑' : ' ↓' : ''}</span>
                      </button> : <span className="p-1">{column.label}</span>}
                    </div>
                    <ColumnResizeHandle column={column} width={widths[column.key]} onResize={resizeColumn} />
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>{tracks.map((track) => <DesktopRow key={track.id} track={track} widths={widths} />)}</tbody>
        </table>
      </div>
      <div className="divide-y divide-border-subtle md:hidden">{tracks.map((track) => <MobileRow key={track.id} track={track} />)}</div>
    </section>
  )
}

function Statuses({ track }: { track: TrackSummary }) {
  return <div className="flex flex-wrap gap-1">{trackStatuses(track).map((status) => <Badge key={status} tone={status === 'Pronto' ? 'added' : status === 'Errore di lettura' || status === 'File mancante' ? 'conflict' : 'neutral'}>{status}</Badge>)}</div>
}

function DesktopRow({ track, widths }: { track: TrackSummary; widths: CatalogColumnWidths }) {
  return <tr className="border-b border-border-subtle align-top hover:bg-surface-raised">
    <td className="p-3" style={{ width: `${widths.title}px` }}><Link className="focus-ring block break-words font-medium text-inherit" to={`/catalog/${track.id}`}>{track.title ?? track.filename}</Link><span className="mt-1 block break-all font-mono text-2xs text-text-muted">{track.filename}</span></td>
    <td className="p-3 break-words text-text-secondary" style={{ width: `${widths.artist}px` }}>{track.artist ?? '—'}</td>
    <td className="p-3 break-words text-text-secondary" style={{ width: `${widths.album}px` }}>{track.album ?? '—'}</td>
    <td className="p-3 text-text-secondary" style={{ width: `${widths.added}px` }}>{track.year ?? '—'}</td>
    <td className="p-3 text-text-secondary" style={{ width: `${widths.format}px` }}>{track.format ?? '—'}</td>
    <td className="p-3 font-mono text-text-secondary" style={{ width: `${widths.duration}px` }}>{formatDuration(track.duration_ms)}</td>
    <td className="p-3" style={{ width: `${widths.status}px` }}><Statuses track={track} /></td>
  </tr>
}

function MobileRow({ track }: { track: TrackSummary }) {
  return <article className="p-4"><Link className="focus-ring block rounded" to={`/catalog/${track.id}`}><h2 className="break-words font-medium">{track.title ?? track.filename}</h2><p className="mt-1 break-words text-sm text-text-secondary">{track.artist ?? 'Artista sconosciuto'}{track.album ? ` · ${track.album}` : ''}</p><p className="mt-2 break-all font-mono text-2xs text-text-muted">{track.path}</p><div className="mt-3 flex items-center justify-between gap-2"><Statuses track={track} /><span className="shrink-0 text-xs text-text-muted">{track.format ?? '—'} · {formatDuration(track.duration_ms)}</span></div></Link></article>
}
