import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ApiError } from '@/lib/api'
import { Badge, Button, EmptyState, Input, Select, SkeletonRows } from '@/components/ui'
import { useTracks } from '@/hooks/useTracks'
import { useDetectDuplicates, useDismissDuplicate, useDuplicateGroups } from '@/hooks/useDuplicates'
import { applyFacets, EMPTY_FACETS, useFacetOptions, type FacetKey, type FacetState } from '@/hooks/useTrackFacets'
import type { SortKey, TrackSummary } from '@/lib/types'

const COLUMNS: Array<{ key: SortKey; label: string }> = [
  { key: 'title', label: 'Titolo' },
  { key: 'artist', label: 'Artista' },
  { key: 'album', label: 'Album' },
  { key: 'added', label: 'Aggiunto' },
]

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
              <div className="hidden overflow-x-auto md:block">
                <table className="w-full min-w-[880px] table-fixed border-collapse text-left">
                  <thead className="border-b border-border-subtle text-xs text-text-muted">
                    <tr>
                      {COLUMNS.map((column) => <th key={column.key} className="p-3 font-medium"><button type="button" className="focus-ring rounded p-1" onClick={() => setSortFromHeader(column.key)} aria-sort={sort === column.key ? sortDirection === 'asc' ? 'ascending' : 'descending' : 'none'}>{column.label}{sort === column.key ? sortDirection === 'asc' ? ' ↑' : ' ↓' : ''}</button></th>)}
                      <th className="w-20 p-3 font-medium">Formato</th><th className="w-20 p-3 font-medium">Durata</th><th className="w-52 p-3 font-medium">Stato</th>
                    </tr>
                  </thead>
                  <tbody>{tracks.map((track) => <DesktopRow key={track.id} track={track} />)}</tbody>
                </table>
              </div>
              <div className="divide-y divide-border-subtle md:hidden">{tracks.map((track) => <MobileRow key={track.id} track={track} />)}</div>
              {hasNextPage ? <div className="p-4 text-center"><Button variant="secondary" disabled={isFetchingNextPage} onClick={() => fetchNextPage()}>{isFetchingNextPage ? 'Caricamento…' : 'Carica altri file'}</Button></div> : null}
            </>}
    </div>
  )
}

function Statuses({ track }: { track: TrackSummary }) {
  return <div className="flex flex-wrap gap-1">{trackStatuses(track).map((status) => <Badge key={status} tone={status === 'Pronto' ? 'added' : status === 'Errore di lettura' || status === 'File mancante' ? 'conflict' : 'neutral'}>{status}</Badge>)}</div>
}

function DesktopRow({ track }: { track: TrackSummary }) {
  return <tr className="border-b border-border-subtle align-top hover:bg-surface-raised">
    <td className="p-3"><Link className="focus-ring block break-words font-medium text-inherit" to={`/catalog/${track.id}`}>{track.title ?? track.filename}</Link><span className="mt-1 block break-all font-mono text-2xs text-text-muted">{track.filename}</span></td>
    <td className="p-3 break-words text-text-secondary">{track.artist ?? '—'}</td><td className="p-3 break-words text-text-secondary">{track.album ?? '—'}</td><td className="p-3 text-text-secondary">{track.year ?? '—'}</td><td className="p-3 text-text-secondary">{track.format ?? '—'}</td><td className="p-3 font-mono text-text-secondary">{formatDuration(track.duration_ms)}</td><td className="p-3"><Statuses track={track} /></td>
  </tr>
}

function MobileRow({ track }: { track: TrackSummary }) {
  return <article className="p-4"><Link className="focus-ring block rounded" to={`/catalog/${track.id}`}><h2 className="break-words font-medium">{track.title ?? track.filename}</h2><p className="mt-1 break-words text-sm text-text-secondary">{track.artist ?? 'Artista sconosciuto'}{track.album ? ` · ${track.album}` : ''}</p><p className="mt-2 break-all font-mono text-2xs text-text-muted">{track.path}</p><div className="mt-3 flex items-center justify-between gap-2"><Statuses track={track} /><span className="shrink-0 text-xs text-text-muted">{track.format ?? '—'} · {formatDuration(track.duration_ms)}</span></div></Link></article>
}
