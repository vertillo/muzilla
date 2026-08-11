import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Button, EmptyState, Modal, ThumbnailTile } from '@/components/ui'
import { applyReviewBundle, undoReviewBundle } from '@/lib/api'
import {
  useReview,
  useReviewCover,
  useReviewCoverUpload,
  useReviewNeighbors,
  useReviewOperationDecisions,
  useReviewOperationEdit,
  useReviewTaskRetry,
} from '@/hooks/useReviews'
import { useJob } from '@/hooks/useJobs'
import { useToasts } from '@/hooks/useToasts'
import type { ReviewOperation } from '@/lib/types'

type SectionKey = 'metadata' | 'path' | 'cover' | 'lyrics' | 'volume' | 'grouping' | 'other'

const SECTION: Record<SectionKey, { title: string; description: string }> = {
  metadata: { title: 'Tag metadata', description: 'Titolo, artista, album e altri tag proposti.' },
  path: { title: 'Nome file e percorso', description: 'Il percorso finale viene controllato prima dell’applicazione.' },
  cover: { title: 'Cover', description: 'La scelta resta una proposta finché non applichi la review.' },
  lyrics: { title: 'Testo', description: 'Il testo conserva sorgente e stato sincronizzato.' },
  volume: { title: 'Analisi volume', description: 'Valori ReplayGain proposti per il file.' },
  grouping: { title: 'Risolvi raccolta', description: 'Scegli una sola correzione compatibile. La preview non cambia il file né il catalogo finché non applichi.' },
  other: { title: 'Altre modifiche', description: 'Operazioni di revisione aggiuntive.' },
}

function sectionFor(operation: ReviewOperation): SectionKey {
  if (operation.kind === 'set_tag') return 'metadata'
  if (operation.kind === 'move_file') return 'path'
  if (operation.kind === 'embed_art' || operation.kind === 'remove_art') return 'cover'
  if (operation.kind === 'write_lyrics') return 'lyrics'
  if (operation.kind === 'set_replay_gain') return 'volume'
  if (operation.kind === 'grouping_correction') return 'grouping'
  return 'other'
}

function formatValue(value: unknown, operation: ReviewOperation): string {
  if (value === null || value === undefined || value === '') return 'Non impostato'
  if (operation.kind === 'write_lyrics' && typeof value === 'object') {
    const lyrics = value as { text?: unknown; synced?: unknown; provider?: unknown }
    return `${lyrics.synced ? 'sincronizzato' : 'testo semplice'} · ${typeof lyrics.provider === 'string' ? lyrics.provider : 'origine non indicata'} · ${typeof lyrics.text === 'string' ? lyrics.text.slice(0, 180) : ''}`
  }
  if ((operation.kind === 'embed_art' || operation.kind === 'remove_art') && typeof value === 'object') {
    const art = value as { blob_id?: unknown }
    return typeof art.blob_id === 'number' ? `Immagine #${art.blob_id}` : 'Immagine proposta'
  }
  if (operation.kind === 'grouping_correction' && typeof value === 'object' && value !== null) {
    const correction = value as { action?: unknown }
    if (correction.action === 'confirm_collection') return 'Conferma la raccolta rilevata'
    if (correction.action === 'treat_as_singleton') return 'Tratta come brano singolo'
    if (correction.action === 'move_to_collection') return 'Usa una raccolta compatibile'
    return 'Raccolta rilevata da verificare'
  }
  return Array.isArray(value) ? value.join(', ') : String(value)
}

function taskLabel(kind: string): string {
  return kind === 'art' || kind === 'cover' ? 'Cover' : kind === 'lyrics' ? 'Testo' : kind === 'replaygain' ? 'Analisi volume' : kind
}

function taskStateLabel(state: string): string {
  return ({ pending: 'In attesa', running: 'In corso', succeeded: 'Pronto', not_found: 'Non trovato', transient_failure: 'Temporaneamente fallito', permanent_failure: 'Fallito permanentemente', cancelled: 'Annullato' } as Record<string, string>)[state] ?? state
}

function validReturnTo(value: string | null): string {
  return !value || !value.startsWith('/') || value.startsWith('//') ? '/reviews' : value
}

function inboxFilters(returnTo: string) {
  const url = new URL(returnTo, window.location.origin)
  return { q: url.searchParams.get('q') ?? undefined, state: url.searchParams.get('state')?.split(',').filter(Boolean), confidence: url.searchParams.get('confidence') ?? undefined, issue: url.searchParams.get('issue') ?? undefined, source: url.searchParams.get('source') ?? undefined }
}

function snapshotText(snapshot: Record<string, unknown> | null | undefined, field: string): string | null {
  const value = snapshot?.[field]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null
}

function explanationSummary(snapshot: Record<string, unknown> | null, explanation: Record<string, unknown> | null): string | null {
  const labels = (value: unknown) => Array.isArray(value)
    ? value.map((item) => typeof item === 'string' ? item : typeof item === 'object' && item !== null && typeof (item as { name?: unknown }).name === 'string' ? (item as { name: string }).name : null).filter((item): item is string => item !== null)
    : []
  const signals = labels(snapshot?.signals)
  const penalties = labels(snapshot?.penalties)
  const rejected = labels(snapshot?.rejection_reasons).concat(labels(explanation?.rejection_reasons))
  const parts = [signals.length ? signals.join(' · ') : null, penalties.length ? `Nota: ${penalties.join(' · ')}` : null, rejected.length ? `Da verificare: ${rejected.join(' · ')}` : null].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

export function ReviewDetail() {
  const { id } = useParams<{ id: string }>()
  const reviewId = id ? Number(id) : NaN
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const returnTo = validReturnTo(search.get('returnTo'))
  const review = useReview(Number.isFinite(reviewId) ? reviewId : null)
  const neighbors = useReviewNeighbors(Number.isFinite(reviewId) ? reviewId : null, inboxFilters(returnTo))
  const decisions = useReviewOperationDecisions(reviewId)
  const cover = useReviewCover(reviewId)
  const upload = useReviewCoverUpload(reviewId)
  const retry = useReviewTaskRetry(reviewId)
  const edit = useReviewOperationEdit(reviewId)
  const toasts = useToasts()
  const [applyJobId, setApplyJobId] = useState<number | null>(null)
  const applyJob = useJob(applyJobId)
  const apply = useMutation({
    mutationFn: () => applyReviewBundle(reviewId),
    onSuccess: (result) => {
      setApplyJobId(result.job_id)
      toasts.push({ tone: 'info', title: 'Applicazione avviata', description: 'L’esito viene verificato file per file.' })
    },
  })
  const [applyConfirmation, setApplyConfirmation] = useState(false)
  const [undoJobId, setUndoJobId] = useState<number | null>(null)
  const undoJob = useJob(undoJobId)
  const undo = useMutation({
    mutationFn: (applyRunId: number) => undoReviewBundle(reviewId, applyRunId),
    onSuccess: (result) => {
      setUndoJobId(result.job_id)
      void review.refetch()
      toasts.push({ tone: 'info', title: 'Ripristino avviato', description: 'Ogni file viene verificato prima del ripristino.' })
    },
  })
  const [undoConfirmation, setUndoConfirmation] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [editing, setEditing] = useState<ReviewOperation | null>(null)
  const [editValue, setEditValue] = useState('')
  const [editSynced, setEditSynced] = useState(false)
  const [focusedOperation, setFocusedOperation] = useState(0)
  const operationRefs = useRef<Array<HTMLDivElement | null>>([])

  const operations = useMemo(() => review.data?.current_revision.operations ?? [], [review.data])
  const grouped = useMemo(() => {
    const result = new Map<SectionKey, ReviewOperation[]>()
    for (const operation of operations) result.set(sectionFor(operation), [...(result.get(sectionFor(operation)) ?? []), operation])
    return result
  }, [operations])
  const orderedOperations = useMemo(() => [...grouped.values()].flat(), [grouped])

  useEffect(() => {
    if (focusedOperation >= orderedOperations.length) setFocusedOperation(Math.max(orderedOperations.length - 1, 0))
  }, [focusedOperation, orderedOperations.length])
  useEffect(() => {
    if (!applyJob.data || !['succeeded', 'failed', 'cancelled'].includes(applyJob.data.state)) return
    void review.refetch()
    if (applyJob.data.state !== 'succeeded') toasts.push({ tone: 'error', title: 'Applicazione non completata', description: applyJob.data.error ?? 'Consulta l’esito della review.' })
  // A terminal job is only the transport outcome; ReviewBundle remains the source for file results.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyJob.data?.state])
  useEffect(() => {
    if (!undoJob.data || !['succeeded', 'failed', 'cancelled'].includes(undoJob.data.state)) return
    void review.refetch()
    if (undoJob.data.state !== 'succeeded') toasts.push({ tone: 'error', title: 'Ripristino non completato', description: undoJob.data.error ?? 'Consulta l’esito persistente della review.' })
  // The transport job may be cancelled after some files were restored; the undo run is authoritative.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [undoJob.data?.state])
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target instanceof HTMLElement ? event.target : null
      if (target?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      const move = (index: number) => {
        const next = Math.max(0, Math.min(index, orderedOperations.length - 1))
        setFocusedOperation(next)
        requestAnimationFrame(() => { operationRefs.current[next]?.focus(); operationRefs.current[next]?.scrollIntoView({ block: 'nearest' }) })
      }
      if (event.key === '?') { event.preventDefault(); setShowShortcuts((visible) => !visible); return }
      if (event.key.toLowerCase() === 'j' && orderedOperations.length) { event.preventDefault(); move(focusedOperation + 1) }
      if (event.key.toLowerCase() === 'k' && orderedOperations.length) { event.preventDefault(); move(focusedOperation - 1) }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [focusedOperation, orderedOperations.length])

  if (!Number.isFinite(reviewId)) return <div className="p-6"><EmptyState title="Review non trovata" /></div>
  if (review.isLoading) return <div className="p-6"><EmptyState title="Caricamento review…" /></div>
  if (review.isError || !review.data) return <div className="p-6"><EmptyState title="Review non disponibile" action={<Button onClick={() => navigate(returnTo)}>Torna alle revisioni</Button>} /></div>

  const data = review.data
  const accepted = operations.filter((operation) => operation.decision === 'accepted').length
  const rejected = operations.filter((operation) => operation.decision === 'rejected').length
  const pending = operations.length - accepted - rejected
  const unfinishedTasks = data.task_attempts.filter((task) => task.state === 'pending' || task.state === 'running')
  const canApply = accepted > 0 && unfinishedTasks.length === 0 && ['ready', 'needs_attention', 'partially_applied', 'failed'].includes(data.state)
  const source = data.source_items[0]
  const snapshot = data.current_revision.candidate_snapshot as Record<string, unknown> | null
  const explanation = data.current_revision.match_explanation as Record<string, unknown> | null
  const latestRun = data.apply_runs.at(-1)
  const latestUndo = data.undo_runs.at(-1)
  const isApplying = data.state === 'applying' || apply.isPending || (applyJob.data?.state === 'pending' || applyJob.data?.state === 'running' || applyJob.data?.state === 'cancelling')
  const isUndoing = undo.isPending || latestUndo?.state === 'pending' || latestUndo?.state === 'undoing' || undoJob.data?.state === 'pending' || undoJob.data?.state === 'running' || undoJob.data?.state === 'cancelling'
  const canStartUndo = !latestUndo && !!latestRun && ['applied', 'partially_applied'].includes(latestRun.state)
  const canRetryUndo = !!latestUndo && ['partially_undone', 'failed'].includes(latestUndo.state) && (latestUndo.result?.files.some((file) => file.retryable) ?? false)
  const latestTasks = new Map<string, typeof data.task_attempts[number]>()
  for (const task of data.task_attempts) latestTasks.set(`${task.kind}:${task.item_key}`, task)
  const retryButtonTaskIds = new Set<number>()
  for (const task of latestTasks.values()) {
    if (task.state === 'transient_failure' && ![...retryButtonTaskIds].some((id) => data.task_attempts.find((item) => item.id === id)?.kind === task.kind)) retryButtonTaskIds.add(task.id)
  }
  const currentTaskIds = new Set([...latestTasks.values()].map((task) => task.id))

  function reviewUrl(nextId: number) { return `/reviews/${nextId}?returnTo=${encodeURIComponent(returnTo)}` }
  function navigateReview(direction: -1 | 1, unreviewed = false) {
    const nextId = direction < 0 ? neighbors.data?.previous_id : unreviewed ? neighbors.data?.next_unreviewed_id : neighbors.data?.next_id
    if (nextId !== null && nextId !== undefined) navigate(reviewUrl(nextId), { replace: true })
  }
  function setDecision(operation: ReviewOperation, decision: 'pending' | 'accepted' | 'rejected') {
    decisions.mutate({ revisionId: data.current_revision.id, decisions: [{ operation_id: operation.id, decision }] })
  }
  function beginEdit(operation: ReviewOperation) {
    setEditing(operation)
    if (operation.kind === 'write_lyrics' && operation.proposed_value && typeof operation.proposed_value === 'object') {
      const lyrics = operation.proposed_value as { text?: unknown; synced?: unknown }
      setEditValue(typeof lyrics.text === 'string' ? lyrics.text : '')
      setEditSynced(lyrics.synced === true)
    } else setEditValue(Array.isArray(operation.proposed_value) ? operation.proposed_value.join(', ') : String(operation.proposed_value ?? ''))
  }
  function saveEdit() {
    if (!editing) return
    const tagValue = Array.isArray(editing.proposed_value)
      ? editValue.split(',').map((item) => item.trim()).filter(Boolean)
      : typeof editing.proposed_value === 'number' ? Number(editValue)
      : typeof editing.proposed_value === 'boolean' ? editValue === 'true'
      : editValue
    const mutation = editing.kind === 'write_lyrics'
      ? { operationId: editing.id, edit: { revision_id: data.current_revision.id, kind: 'write_lyrics' as const, text: editValue, synced: editSynced } }
      : { operationId: editing.id, edit: { revision_id: data.current_revision.id, kind: 'set_tag' as const, value: tagValue } }
    edit.mutate(mutation, { onSuccess: () => setEditing(null) })
  }

  return (
    <div className="min-h-0 pb-28">
      <header className="border-b border-border-subtle p-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-text-secondary"><Link className="focus-ring rounded text-inherit" to={returnTo}>Revisioni</Link><span aria-hidden="true">/</span><span>Review {data.id}</span></div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => navigate(-1)}>Indietro</Button><Button size="sm" variant="ghost" onClick={() => navigate(returnTo)}>Chiudi</Button>
          <span className="ml-auto text-sm text-text-secondary">{data.state === 'applying' ? 'Applicazione in corso' : 'Review aperta'}</span>
          <Button size="sm" variant="secondary" disabled={!neighbors.data?.previous_id} onClick={() => navigateReview(-1)}>Precedente</Button>
          <Button size="sm" variant="secondary" disabled={!neighbors.data?.next_unreviewed_id} onClick={() => navigateReview(1, true)}>Successiva non revisionata</Button>
          <Button size="sm" variant="secondary" disabled={!neighbors.data?.next_id} onClick={() => navigateReview(1)}>Successiva</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowShortcuts(true)}>Scorciatoie</Button>
        </div>
      </header>

      <section className="border-b border-border-subtle p-4 sm:p-5" aria-labelledby="source-heading"><div className="flex gap-4"><ThumbnailTile src={source?.cover_thumbnail_url ?? undefined} size={72} label={source?.cover_thumbnail_url ? 'Cover corrente' : 'Nessuna cover corrente'} /><div className="min-w-0"><h1 id="source-heading" className="break-words text-xl font-semibold">{source?.filename ?? data.title}</h1><p className="mt-1 break-all font-mono text-xs text-text-secondary">{source?.path ?? 'Percorso sorgente non disponibile nello snapshot'}</p><p className="mt-2 text-sm text-text-secondary">{source?.format ? `Formato: ${source.format.toUpperCase()}` : 'Formato non disponibile'} · Stato: {data.state}</p></div></div></section>

      <section className="border-b border-border-subtle p-4 sm:p-5" aria-labelledby="candidate-heading"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 id="candidate-heading" className="text-base font-semibold">Candidato</h2>{snapshot ? <><p className="mt-1 text-sm text-text-primary">{[snapshotText(snapshot, 'artist'), snapshotText(snapshot, 'title')].filter(Boolean).join(' — ') || 'Candidato selezionato'}</p><p className="mt-1 text-sm text-text-secondary">{[snapshotText(snapshot, 'album'), snapshotText(snapshot, 'year'), snapshotText(snapshot, 'duration_ms') && `${snapshotText(snapshot, 'duration_ms')} ms`, snapshotText(snapshot, 'position') && `traccia ${snapshotText(snapshot, 'position')}`, snapshotText(snapshot, 'track_count') && `${snapshotText(snapshot, 'track_count')} brani`].filter(Boolean).join(' · ')}</p><p className="mt-2 text-sm text-text-secondary">{data.current_revision.confidence === null ? 'Selezione manuale' : `Confidenza ${Math.round(data.current_revision.confidence * 100)}%`}</p>{explanationSummary(snapshot, explanation) && <p className="mt-1 text-sm text-text-secondary">Perché: {explanationSummary(snapshot, explanation)}</p>}</> : <p className="mt-1 text-sm text-text-secondary">Nessun candidato selezionato. Cerca o inserisci un riferimento manuale.</p>}</div><Button size="sm" variant="secondary" onClick={() => navigate(`/reviews/${data.id}/search?returnTo=${encodeURIComponent(location.pathname + location.search)}`)}>Cerca o cambia candidato</Button></div></section>

      <div className="space-y-5 p-4 sm:p-5">
        {decisions.isError && <div role="alert" className="rounded-md border border-diff-conflict p-3 text-sm">La revisione è cambiata durante il salvataggio. Ricarica i dati correnti.</div>}
        {data.error && <div role="alert" className="rounded-md border border-diff-conflict p-3 text-sm">Problema della review: {data.error}</div>}
        <section className="rounded-md border border-border-subtle p-4" aria-labelledby="cover-heading"><h2 id="cover-heading" className="font-semibold">Cover</h2><div className="mt-3 flex flex-wrap gap-3"><div><ThumbnailTile src={source?.cover_thumbnail_url ?? undefined} label="Cover corrente" /><p className="mt-1 text-xs text-text-secondary">Corrente</p></div>{data.cover_candidates.map((candidate) => <div key={candidate.id}><ThumbnailTile src={candidate.thumbnail_url} label={`Cover candidata ${candidate.provider}`} /><p className="mt-1 text-xs text-text-secondary">{candidate.provider} · {candidate.width}×{candidate.height}</p><Button size="sm" variant="secondary" disabled={cover.isPending} onClick={() => cover.mutate({ action: 'select', assetCandidateId: candidate.id })}>Usa</Button></div>)}</div><div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="secondary" disabled={cover.isPending} onClick={() => cover.mutate({ action: 'keep' })}>Mantieni</Button><Button size="sm" variant="ghost" disabled={cover.isPending} onClick={() => cover.mutate({ action: 'remove' })}>Rimuovi</Button><label className="focus-within:ring-2 focus-within:ring-accent rounded text-sm"><span className="sr-only">Carica cover JPEG o PNG</span><input type="file" accept="image/jpeg,image/png" disabled={upload.isPending} onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.currentTarget.value = '' }} /> </label></div>{(cover.isError || upload.isError) && <p role="alert" className="mt-2 text-sm text-diff-removed">Impossibile aggiornare la cover. Il file musicale non è stato modificato.</p>}</section>

        {([...grouped.entries()] as Array<[SectionKey, ReviewOperation[]]>).map(([key, sectionOperations]) => <section key={key} className="rounded-md border border-border-subtle" aria-labelledby={`section-${key}`}><div className="border-b border-border-subtle px-4 py-3"><h2 id={`section-${key}`} className="font-semibold">{SECTION[key].title} <span className="font-normal text-text-secondary">· {sectionOperations.length} modifiche</span></h2><p className="mt-1 text-sm text-text-secondary">{SECTION[key].description}</p></div>{sectionOperations.map((operation) => { const index = orderedOperations.findIndex((item) => item.id === operation.id); const editable = operation.kind === 'set_tag' || operation.kind === 'write_lyrics'; return <div key={operation.id} ref={(element) => { operationRefs.current[index] = element }} tabIndex={index === focusedOperation ? 0 : -1} onFocus={() => setFocusedOperation(index)} className="focus-ring border-b border-border-subtle p-4 last:border-b-0"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{operation.kind === 'grouping_correction' ? formatValue(operation.proposed_value, operation) : operation.field}</h3><div className="flex gap-1" aria-label={`Decisione per ${operation.field}`}>{editable && <Button size="sm" variant="ghost" disabled={edit.isPending} onClick={() => beginEdit(operation)}>Modifica</Button>}{(['accepted', 'pending', 'rejected'] as const).map((decision) => <Button key={decision} size="sm" variant={operation.decision === decision ? 'secondary' : 'ghost'} disabled={decisions.isPending || isApplying} onClick={() => setDecision(operation, decision)}>{decision === 'accepted' ? operation.kind === 'grouping_correction' ? 'Scegli' : 'Accetta' : decision === 'rejected' ? operation.kind === 'grouping_correction' ? 'Escludi' : 'Rifiuta' : 'In attesa'}</Button>)}</div></div><dl className="mt-3 grid gap-3 text-sm md:grid-cols-2"><div><dt className="text-text-secondary">Nel file</dt><dd className="mt-1 break-words">{formatValue(operation.current_value, operation)}</dd></div><div><dt className="text-text-secondary">Proposto</dt><dd className="mt-1 break-words">{formatValue(operation.proposed_value, operation)}</dd></div></dl></div> })}</section>)}

        {data.task_attempts.length > 0 && <section className="rounded-md border border-border-subtle p-4" aria-labelledby="task-heading"><h2 id="task-heading" className="font-semibold">Stato preparazione</h2><ul className="mt-3 space-y-2 text-sm">{data.task_attempts.map((task) => <li key={task.id} className="flex flex-wrap items-center gap-x-2"><strong>{taskLabel(task.kind)}</strong><span>{taskStateLabel(task.state)}</span>{task.error && <span className="text-text-secondary">· {task.error}</span>}{retryButtonTaskIds.has(task.id) && <Button size="sm" variant="secondary" disabled={retry.isPending} onClick={() => retry.mutate(task.kind)}>Riprova</Button>}{currentTaskIds.has(task.id) && task.state === 'not_found' && <Button size="sm" variant="ghost" onClick={() => navigate(`/reviews/${data.id}/search?returnTo=${encodeURIComponent(location.pathname + location.search)}`)}>Cerca manualmente</Button>}</li>)}</ul></section>}

        {latestRun && <section className="rounded-md border border-border-subtle p-4" aria-labelledby="apply-result-heading"><h2 id="apply-result-heading" className="font-semibold">Esito applicazione</h2><p className="mt-1 text-sm text-text-secondary">{latestRun.state}</p>{latestRun.error && <p role="alert" className="mt-2 text-sm text-diff-removed">{latestRun.error}</p>}{latestRun.result?.files.map((file) => <div key={file.track_id} className="mt-2 text-sm"><strong>File #{file.track_id}</strong> · {file.state}{file.applied_operation_ids.length > 0 && ` · operazioni ${file.applied_operation_ids.join(', ')}`}{file.error && <span className="text-diff-removed"> · {file.error}</span>}</div>)}{['partially_applied', 'failed'].includes(latestRun.state) && <div className="mt-3"><Button size="sm" variant="secondary" disabled={isApplying} onClick={() => apply.mutate()}>Riprova solo i file falliti</Button></div>}</section>}
        {(latestUndo || canStartUndo) && <section className="rounded-md border border-border-subtle p-4" aria-labelledby="undo-result-heading"><h2 id="undo-result-heading" className="font-semibold">Ripristino applicazione</h2>{latestUndo ? <><p className="mt-1 text-sm text-text-secondary">{latestUndo.state}</p>{latestUndo.error && <p role="alert" className="mt-2 text-sm text-diff-removed">{latestUndo.error}</p>}{latestUndo.result?.files.map((file) => <div key={file.track_id} className="mt-2 text-sm"><strong>File #{file.track_id}</strong> · {file.state}{file.error && <span className="text-diff-removed"> · {file.error}</span>}{file.retryable && <span> · riprovabile</span>}</div>)}</> : <p className="mt-1 text-sm text-text-secondary">L’applicazione può essere ripristinata finché journal e file superano i controlli.</p>}{undo.isError && <p role="alert" className="mt-2 text-sm text-diff-removed">Impossibile avviare il ripristino. Nessun altro file è stato modificato.</p>}<div className="mt-3">{canStartUndo && <Button size="sm" variant="secondary" disabled={isUndoing} onClick={() => setUndoConfirmation(true)}>Ripristina applicazione</Button>}{canRetryUndo && latestRun && <Button size="sm" variant="secondary" disabled={isUndoing} onClick={() => undo.mutate(latestRun.id)}>Riprova solo i file non ripristinati</Button>}{isUndoing && <span className="text-sm text-text-secondary">Ripristino in corso…</span>}</div></section>}
      </div>

      <div className="sticky bottom-0 z-20 border-t border-border-default bg-surface-raised px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-md sm:px-5"><div className="flex flex-col gap-3 sm:flex-row sm:items-center"><p className="text-sm"><strong>{accepted} accettate</strong> · {rejected} rifiutate · {pending} in attesa{unfinishedTasks.length > 0 ? ` · ${unfinishedTasks.length} attività ancora in corso` : ''}</p><div className="flex flex-wrap gap-2 sm:ml-auto"><Button variant="secondary" onClick={() => decisions.mutate({ revisionId: data.current_revision.id, decisions: operations.map((operation) => ({ operation_id: operation.id, decision: 'rejected' })) })} disabled={decisions.isPending || operations.length === 0 || isApplying}>Rifiuta tutte</Button><Button disabled={!canApply || isApplying} onClick={() => setApplyConfirmation(true)}>{isApplying ? 'Applicazione in corso…' : `Applica ${accepted} modifiche`}</Button></div></div>{!canApply && <p className="mt-2 text-sm text-text-secondary">{accepted === 0 ? 'Accetta almeno una modifica per applicare.' : unfinishedTasks.length > 0 ? 'Attendi il completamento delle attività opzionali prima di applicare.' : 'Questa review non è pronta per l’applicazione.'}</p>}</div>

      <Modal open={applyConfirmation} title="Applicare le modifiche?" onClose={() => setApplyConfirmation(false)} footer={<><Button variant="ghost" onClick={() => setApplyConfirmation(false)}>Annulla</Button><Button onClick={() => { apply.mutate(); setApplyConfirmation(false) }}>Applica {accepted} modifiche</Button></>}>
        Verranno applicate {accepted} modifiche ai file indicati. L’esito persistente mostrerà ogni file e operazione.
      </Modal>
      <Modal open={undoConfirmation} title="Ripristinare l’applicazione?" onClose={() => setUndoConfirmation(false)} footer={<><Button variant="ghost" onClick={() => setUndoConfirmation(false)}>Annulla</Button><Button disabled={!latestRun || undo.isPending} onClick={() => { if (latestRun) undo.mutate(latestRun.id); setUndoConfirmation(false) }}>Ripristina file</Button></>}>
        Verranno eseguite in ordine inverso soltanto le operazioni riuscite. Ogni file deve essere ancora identico allo stato successivo all’applicazione; collisioni, modifiche esterne o recovery incerta bloccano il file senza sovrascriverlo. Il ripristino è atomico per file, non per l’intera review.
      </Modal>
      <Modal open={showShortcuts} title="Scorciatoie" onClose={() => setShowShortcuts(false)}>
        <ul className="space-y-2"><li><kbd>J</kbd>/<kbd>K</kbd> sposta focus e viewport fra le modifiche.</li><li><kbd>[</kbd>/<kbd>]</kbd> apre la review precedente o successiva.</li></ul>
      </Modal>
      <Modal open={editing !== null} title={editing?.kind === 'write_lyrics' ? 'Modifica testo' : `Modifica ${editing?.field ?? ''}`} onClose={() => setEditing(null)} footer={<><Button variant="ghost" onClick={() => setEditing(null)}>Annulla</Button><Button disabled={edit.isPending} onClick={saveEdit}>Salva modifica</Button></>}>
        {editing?.kind === 'write_lyrics' ? <label className="block text-sm">Testo<textarea aria-label="Testo lyrics" className="mt-2 min-h-40 w-full rounded border border-border-default bg-surface p-2" value={editValue} onChange={(event) => setEditValue(event.target.value)} /><span className="mt-2 flex gap-2"><input type="checkbox" checked={editSynced} onChange={(event) => setEditSynced(event.target.checked)} />Sincronizzato (ogni riga deve iniziare con timestamp LRC)</span></label> : typeof editing?.proposed_value === 'boolean' ? <label className="mt-2 flex gap-2 text-sm"><input aria-label="Valore tag booleano" type="checkbox" checked={editValue === 'true'} onChange={(event) => setEditValue(String(event.target.checked))} />Valore attivo</label> : <label className="block text-sm">Valore<input aria-label="Valore tag" type={typeof editing?.proposed_value === 'number' ? 'number' : editing?.field === 'date' ? 'date' : 'text'} className="mt-2 w-full rounded border border-border-default bg-surface p-2" value={editValue} onChange={(event) => setEditValue(event.target.value)} /></label>}
        {edit.isError && <p role="alert" className="mt-2 text-sm text-diff-removed">La modifica non è valida; nessun file è stato scritto.</p>}
      </Modal>
    </div>
  )
}
