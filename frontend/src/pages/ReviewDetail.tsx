import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Button, EmptyState, Modal, ThumbnailTile } from '@/components/ui'
import { applyReviewBundle } from '@/lib/api'
import { useReview, useReviewInbox, useReviewOperationDecisions } from '@/hooks/useReviews'
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
    const mode = lyrics.synced ? 'sincronizzato' : 'testo semplice'
    return `${mode} · ${typeof lyrics.provider === 'string' ? lyrics.provider : 'origine non indicata'} · ${typeof lyrics.text === 'string' ? lyrics.text.slice(0, 180) : ''}`
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
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

function taskLabel(kind: string): string {
  return kind === 'art' ? 'Cover' : kind === 'lyrics' ? 'Testo' : kind === 'replaygain' ? 'Analisi volume' : kind
}

function taskStateLabel(state: string): string {
  const labels: Record<string, string> = {
    pending: 'In attesa', running: 'In corso', succeeded: 'Pronto', not_found: 'Non trovato',
    transient_failure: 'Temporaneamente fallito', permanent_failure: 'Fallito permanentemente', cancelled: 'Annullato',
  }
  return labels[state] ?? state
}

function validReturnTo(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/reviews'
  return value
}

function inboxFilters(returnTo: string) {
  const url = new URL(returnTo, window.location.origin)
  return {
    q: url.searchParams.get('q') ?? undefined,
    state: url.searchParams.get('state')?.split(',').filter(Boolean),
    confidence: url.searchParams.get('confidence') ?? undefined,
    issue: url.searchParams.get('issue') ?? undefined,
    source: url.searchParams.get('source') ?? undefined,
  }
}

export function ReviewDetail() {
  const { id } = useParams<{ id: string }>()
  const reviewId = id ? Number(id) : NaN
  const [search] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const returnTo = validReturnTo(search.get('returnTo'))
  const review = useReview(Number.isFinite(reviewId) ? reviewId : null)
  const surrounding = useReviewInbox(inboxFilters(returnTo))
  const decisions = useReviewOperationDecisions(reviewId)
  const toasts = useToasts()
  const apply = useMutation({
    mutationFn: () => applyReviewBundle(reviewId),
    onSuccess: (result) => toasts.push({
      tone: 'info',
      title: 'Applicazione avviata',
      description: `Attività #${result.job_id}: le modifiche restano tracciate nella review.`,
    }),
  })
  const [applyConfirmation, setApplyConfirmation] = useState(false)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const [focusedOperation, setFocusedOperation] = useState(0)
  const operationRefs = useRef<Array<HTMLDivElement | null>>([])

  const operations = useMemo(() => review.data?.current_revision.operations ?? [], [review.data])
  const grouped = useMemo(() => {
    const result = new Map<SectionKey, ReviewOperation[]>()
    for (const operation of operations) {
      const section = sectionFor(operation)
      result.set(section, [...(result.get(section) ?? []), operation])
    }
    return result
  }, [operations])
  const orderedOperations = useMemo(() => [...grouped.values()].flat(), [grouped])
  const currentIndex = surrounding.data?.items.findIndex((item) => item.id === reviewId) ?? -1

  useEffect(() => {
    if (focusedOperation >= orderedOperations.length) setFocusedOperation(Math.max(orderedOperations.length - 1, 0))
  }, [focusedOperation, orderedOperations.length])

  useEffect(() => {
    function isShortcutBlocked(target: EventTarget | null) {
      if (!(target instanceof HTMLElement)) return false
      return Boolean(target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]'))
    }
    function moveFocus(nextIndex: number) {
      const bounded = Math.max(0, Math.min(nextIndex, orderedOperations.length - 1))
      setFocusedOperation(bounded)
      requestAnimationFrame(() => {
        const target = operationRefs.current[bounded]
        target?.focus()
        target?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
      })
    }
    function onKeyDown(event: KeyboardEvent) {
      if (isShortcutBlocked(event.target)) return
      if (event.key === '?') {
        event.preventDefault()
        setShowShortcuts((visible) => !visible)
      } else if (event.key.toLowerCase() === 'j' && orderedOperations.length) {
        event.preventDefault()
        moveFocus(focusedOperation + 1)
      } else if (event.key.toLowerCase() === 'k' && orderedOperations.length) {
        event.preventDefault()
        moveFocus(focusedOperation - 1)
      } else if (event.key === '[') {
        navigateReview(-1)
      } else if (event.key === ']') {
        navigateReview(event.shiftKey ? 1 : 1, event.shiftKey)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  // navigateReview is intentionally reconstructed from current URL state below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedOperation, orderedOperations.length, surrounding.data, returnTo, reviewId])

  if (!Number.isFinite(reviewId)) return <div className="p-6"><EmptyState title="Review non trovata" /></div>
  if (review.isLoading) return <div className="p-6"><EmptyState title="Caricamento review…" /></div>
  if (review.isError || !review.data) return <div className="p-6"><EmptyState title="Review non disponibile" action={<Button onClick={() => navigate(returnTo)}>Torna alle revisioni</Button>} /></div>

  const data = review.data
  const accepted = operations.filter((operation) => operation.decision === 'accepted').length
  const rejected = operations.filter((operation) => operation.decision === 'rejected').length
  const pending = operations.length - accepted - rejected
  const unfinishedTasks = data.task_attempts.filter((task) => task.state === 'pending' || task.state === 'running')
  const canApply = accepted > 0 && unfinishedTasks.length === 0 && ['ready', 'needs_attention'].includes(data.state)
  const source = data.source_items[0]

  function reviewUrl(nextId: number) {
    return `/reviews/${nextId}?returnTo=${encodeURIComponent(returnTo)}`
  }
  function navigateReview(direction: -1 | 1, unreviewed = false) {
    const items = surrounding.data?.items ?? []
    if (currentIndex < 0) return
    const candidates = direction > 0 ? items.slice(currentIndex + 1) : items.slice(0, currentIndex).reverse()
    const next = unreviewed ? candidates.find((item) => item.pending_operations > 0) : candidates[0]
    if (next) navigate(reviewUrl(next.id), { replace: true })
  }
  function setDecision(operation: ReviewOperation, decision: 'pending' | 'accepted' | 'rejected') {
    decisions.mutate({
      revisionId: data.current_revision.id,
      decisions: [{ operation_id: operation.id, decision }],
    })
  }

  return (
    <div className="min-h-0 pb-28">
      <header className="border-b border-border-subtle p-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
          <Link className="focus-ring rounded text-inherit" to={returnTo}>Revisioni</Link>
          <span aria-hidden="true">/</span>
          <span>Review {data.id}</span>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => navigate(-1)}>Indietro</Button>
          <Button size="sm" variant="ghost" onClick={() => navigate(returnTo)}>Chiudi</Button>
          <span className="ml-auto text-sm text-text-secondary">{currentIndex >= 0 && surrounding.data ? `${currentIndex + 1} di ${surrounding.data.items.length}` : 'Review aperta'}</span>
          <Button size="sm" variant="secondary" disabled={currentIndex <= 0} onClick={() => navigateReview(-1)}>Precedente</Button>
          <Button size="sm" variant="secondary" disabled={currentIndex < 0 || currentIndex >= (surrounding.data?.items.length ?? 0) - 1} onClick={() => navigateReview(1, true)}>Successiva non revisionata</Button>
          <Button size="sm" variant="secondary" disabled={currentIndex < 0 || currentIndex >= (surrounding.data?.items.length ?? 0) - 1} onClick={() => navigateReview(1)}>Successiva</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowShortcuts(true)}>Scorciatoie</Button>
        </div>
      </header>

      <section className="border-b border-border-subtle p-4 sm:p-5" aria-labelledby="source-heading">
        <div className="flex gap-4">
          <ThumbnailTile src={data.cover_candidates[0]?.thumbnail_url} size={72} label={data.cover_candidates.length ? 'Cover candidata' : 'Nessuna cover disponibile'} />
          <div className="min-w-0">
            <h1 id="source-heading" className="break-words text-xl font-semibold">{source?.filename ?? data.title}</h1>
            <p className="mt-1 break-all font-mono text-xs text-text-secondary">{source?.path ?? 'Percorso sorgente non disponibile nello snapshot'}</p>
            <p className="mt-2 text-sm text-text-secondary">{source?.format ? `Formato: ${source.format.toUpperCase()}` : 'Formato non disponibile'} · Stato: {data.state}</p>
          </div>
        </div>
      </section>

      <section className="border-b border-border-subtle p-4 sm:p-5" aria-labelledby="candidate-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="candidate-heading" className="text-base font-semibold">Candidato</h2>
            {data.current_revision.candidate_source ? (
              <p className="mt-1 text-sm text-text-secondary">{data.current_revision.candidate_source} · riferimento {data.current_revision.candidate_ref ?? 'non disponibile'} · punteggio non disponibile per questa review</p>
            ) : <p className="mt-1 text-sm text-text-secondary">Nessun candidato selezionato. Puoi cercarne uno senza modificare il file.</p>}
          </div>
          <Button size="sm" variant="secondary" onClick={() => navigate(`/reviews/${data.id}/search?returnTo=${encodeURIComponent(location.pathname + location.search)}`)}>Cerca o cambia candidato</Button>
        </div>
      </section>

      <div className="p-4 sm:p-5">
        {decisions.isError && <div role="alert" className="mb-4 rounded-md border border-diff-conflict p-3 text-sm text-text-primary">La revisione è cambiata durante il salvataggio. I dati correnti sono stati ricaricati.</div>}
        {data.error && <div role="alert" className="mb-4 rounded-md border border-diff-conflict p-3 text-sm text-text-primary">Problema della review: {data.error}</div>}
        {([...grouped.entries()] as Array<[SectionKey, ReviewOperation[]]>).map(([key, sectionOperations]) => (
          <section key={key} className="mb-5 rounded-md border border-border-subtle" aria-labelledby={`section-${key}`}>
            <div className="border-b border-border-subtle px-4 py-3">
              <h2 id={`section-${key}`} className="font-semibold">{SECTION[key].title} <span className="font-normal text-text-secondary">· {sectionOperations.length} modifiche</span></h2>
              <p className="mt-1 text-sm text-text-secondary">{SECTION[key].description}</p>
            </div>
            {sectionOperations.map((operation) => {
              const index = orderedOperations.findIndex((item) => item.id === operation.id)
              return (
                <div
                  key={operation.id}
                  ref={(element) => { operationRefs.current[index] = element }}
                  tabIndex={index === focusedOperation ? 0 : -1}
                  onFocus={() => setFocusedOperation(index)}
                  className="focus-ring border-b border-border-subtle p-4 last:border-b-0"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="font-medium">{operation.kind === 'grouping_correction' ? formatValue(operation.proposed_value, operation) : operation.field}</h3>
                    <div className="flex gap-1" aria-label={`Decisione per ${operation.field}`}>
                      {(['accepted', 'pending', 'rejected'] as const).map((decision) => (
                        <Button key={decision} size="sm" variant={operation.decision === decision ? 'secondary' : 'ghost'} disabled={decisions.isPending} onClick={() => setDecision(operation, decision)}>
                          {decision === 'accepted' ? operation.kind === 'grouping_correction' ? 'Scegli' : 'Accetta' : decision === 'rejected' ? operation.kind === 'grouping_correction' ? 'Escludi' : 'Rifiuta' : 'In attesa'}
                        </Button>
                      ))}
                    </div>
                  </div>
                  <dl className="mt-3 grid gap-3 text-sm md:grid-cols-2">
                    <div><dt className="text-text-secondary">{operation.kind === 'grouping_correction' ? 'Raccolta rilevata' : 'Nel file'}</dt><dd className="mt-1 break-words text-text-primary">{formatValue(operation.current_value, operation)}</dd></div>
                    <div><dt className="text-text-secondary">Proposto</dt><dd className="mt-1 break-words text-text-primary">{formatValue(operation.proposed_value, operation)}</dd></div>
                  </dl>
                  {Object.keys(operation.validation).length > 0 && <p className="mt-3 text-sm text-text-secondary">Controllo: {operation.validation.collision ? 'possibile collisione di percorso' : 'verificato'}</p>}
                </div>
              )
            })}
          </section>
        ))}

        {data.task_attempts.length > 0 && (
          <section className="rounded-md border border-border-subtle p-4" aria-labelledby="task-heading">
            <h2 id="task-heading" className="font-semibold">Stato preparazione</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {data.task_attempts.map((task) => <li key={task.id} className="flex flex-wrap gap-x-2"><strong>{taskLabel(task.kind)}</strong><span>{taskStateLabel(task.state)}</span>{task.error && <span className="text-text-secondary">· {task.error}</span>}</li>)}
            </ul>
          </section>
        )}
      </div>

      <div className="sticky bottom-0 z-20 border-t border-border-default bg-surface-raised px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-md sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <p className="text-sm text-text-primary"><strong>{accepted} accettate</strong> · {rejected} rifiutate · {pending} in attesa{unfinishedTasks.length > 0 ? ` · ${unfinishedTasks.length} attività ancora in corso` : ''}</p>
          <div className="flex flex-wrap gap-2 sm:ml-auto">
            <Button variant="secondary" onClick={() => decisions.mutate({ revisionId: data.current_revision.id, decisions: operations.map((operation) => ({ operation_id: operation.id, decision: 'rejected' })) })} disabled={decisions.isPending || operations.length === 0}>Rifiuta tutte</Button>
            <Button disabled={!canApply || apply.isPending} onClick={() => setApplyConfirmation(true)}>{apply.isPending ? 'Applicazione…' : `Applica ${accepted} modifiche`}</Button>
          </div>
        </div>
        {!canApply && <p className="mt-2 text-sm text-text-secondary">{accepted === 0 ? 'Accetta almeno una modifica per applicare.' : unfinishedTasks.length > 0 ? 'Attendi il completamento delle attività opzionali prima di applicare.' : 'Questa review non è pronta per l’applicazione.'}</p>}
      </div>

      <Modal open={applyConfirmation} title="Applicare le modifiche?" onClose={() => setApplyConfirmation(false)} footer={<><Button variant="ghost" onClick={() => setApplyConfirmation(false)}>Annulla</Button><Button onClick={() => { apply.mutate(); setApplyConfirmation(false) }}>Applica {accepted} modifiche</Button></>}>
        Verranno applicate {accepted} modifiche ai file indicati. Le modifiche rifiutate e in attesa non verranno scritte.
      </Modal>
      <Modal open={showShortcuts} title="Scorciatoie" onClose={() => setShowShortcuts(false)}>
        <ul className="space-y-2"><li><kbd>J</kbd>/<kbd>K</kbd> sposta focus e viewport fra le modifiche.</li><li><kbd>[</kbd>/<kbd>]</kbd> apre la review precedente o successiva.</li><li><kbd>Shift</kbd>+<kbd>]</kbd> apre la successiva non revisionata.</li><li><kbd>Esc</kbd> chiude questo pannello.</li></ul>
      </Modal>
    </div>
  )
}
