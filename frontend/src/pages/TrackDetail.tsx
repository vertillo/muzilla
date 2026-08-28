import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, EmptyState, Input, SkeletonRows } from '@/components/ui'
import { createGroupingReview, createManualTrackReview } from '@/lib/api'
import { useRescanTrack, useTrack } from '@/hooks/useTracks'
import { useJob } from '@/hooks/useJobs'

function status(track: { missing_since: string | null; probe_error: string | null; has_embedded_art: boolean; has_lyrics: boolean }) {
  if (track.missing_since) return 'File mancante'
  if (track.probe_error) return 'Errore di lettura'
  if (!track.has_embedded_art || !track.has_lyrics) return 'Da completare'
  return 'Pronto'
}

export function TrackDetail() {
  const { trackId } = useParams<{ trackId: string }>()
  const id = trackId && Number.isInteger(Number(trackId)) ? Number(trackId) : null
  const navigate = useNavigate()
  const track = useTrack(id)
  const rescan = useRescanTrack()
  const [showCandidates, setShowCandidates] = useState(false)
  const [showManual, setShowManual] = useState(false)
  const [analysisJobId, setAnalysisJobId] = useState<number | null>(null)
  const [manualTitle, setManualTitle] = useState('')
  const [manualArtist, setManualArtist] = useState('')
  const analysisJob = useJob(analysisJobId)
  const manualReview = useMutation({
    mutationFn: (fields: Record<string, unknown>) => createManualTrackReview(id as number, fields),
    onSuccess: (review) => navigate(`/reviews/${review.id}?returnTo=${encodeURIComponent(`/catalog/${id}`)}`),
  })
  const groupingReview = useMutation({
    mutationFn: () => createGroupingReview(id as number),
    onSuccess: (review) => navigate(`/reviews/${review.id}?returnTo=${encodeURIComponent(`/catalog/${id}`)}`),
  })

  useEffect(() => {
    if (analysisJob.data?.state !== 'succeeded') return
    setShowCandidates(true)
    setAnalysisJobId(null)
  }, [analysisJob.data?.state])

  if (id === null) return <div className="p-6"><EmptyState title="File non valido" action={<Button onClick={() => navigate('/catalog')}>Torna al catalogo</Button>} /></div>
  if (track.isLoading) return <SkeletonRows />
  if (track.isError || !track.data) return <div className="p-6"><EmptyState title="File non trovato" action={<Button onClick={() => navigate('/catalog')}>Torna al catalogo</Button>} /></div>
  const file = track.data

  return <div className="min-h-0">
    <header className="border-b border-border-subtle p-4 sm:p-5">
      <Link className="focus-ring rounded text-sm text-text-secondary" to="/catalog">Catalogo</Link>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h1 className="break-words text-xl font-semibold">{file.title ?? file.filename}</h1><p className="mt-1 break-words text-sm text-text-secondary">{file.artist ?? 'Artista sconosciuto'}{file.album ? ` · ${file.album}` : ''}</p><p className="mt-2 break-all font-mono text-2xs text-text-muted">{file.path}</p></div><Badge tone={status(file) === 'Pronto' ? 'added' : 'conflict'}>{status(file)}</Badge></div>
    </header>

    <main className="mx-auto max-w-4xl space-y-5 p-4 sm:p-5">
      {file.missing_since ? <section className="rounded-md border border-diff-conflict p-4"><h2 className="font-semibold">File non disponibile</h2><p className="mt-2 text-sm text-text-secondary">Ultimo percorso: {file.path}. Ultima lettura: {new Date(file.last_scanned_at).toLocaleString()}.</p><div className="mt-3"><Button disabled={rescan.isPending} onClick={() => rescan.mutate(file.id)}>{rescan.isPending ? 'Verifica…' : 'Verifica di nuovo'}</Button></div></section> : <>
        <section className="rounded-md border border-border-subtle p-4"><h2 className="font-semibold">Azioni sul file</h2><p className="mt-1 text-sm text-text-secondary">Ogni azione ha un esito separato: rileggere non cerca provider e cercare non modifica il file.</p><div className="mt-4 flex flex-wrap gap-2"><Button variant="secondary" disabled={rescan.isPending} onClick={() => rescan.mutate(file.id)}>{rescan.isPending ? 'Rilettura…' : 'Rileggi file'}</Button><Button variant="secondary" onClick={() => setShowCandidates(true)}>Cerca corrispondenze</Button><Button variant="secondary" onClick={() => setShowManual(true)}>Modifica manualmente</Button>{file.grouping_needs_resolution && <Button variant="secondary" disabled={groupingReview.isPending} onClick={() => groupingReview.mutate()}>{groupingReview.isPending ? 'Apertura…' : 'Risolvi raccolta'}</Button>}<Button disabled={rescan.isPending || ['pending', 'running', 'cancelling'].includes(analysisJob.data?.state ?? '')} onClick={() => rescan.mutate(file.id, { onSuccess: (job) => setAnalysisJobId(job.job_id) })}>{['pending', 'running', 'cancelling'].includes(analysisJob.data?.state ?? '') ? 'Analisi in corso…' : 'Analizza di nuovo'}</Button></div>{rescan.data ? <p className="mt-3 text-sm text-text-secondary">Rilettura avviata come attività #{rescan.data.job_id}; al termine aggiorna tag e disponibilità del file.</p> : null}{analysisJob.data?.state === 'failed' || analysisJob.data?.state === 'cancelled' ? <p role="alert" className="mt-3 text-sm text-diff-removed">La rilettura non è terminata: la ricerca non è stata avviata.</p> : null}</section>
        <section className="rounded-md border border-border-subtle p-4"><h2 className="font-semibold">Nel catalogo</h2><dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-text-secondary">Formato</dt><dd>{file.format ?? '—'}</dd></div><div><dt className="text-text-secondary">Anno</dt><dd>{file.year ?? '—'}</dd></div><div><dt className="text-text-secondary">Cover</dt><dd>{file.has_embedded_art ? 'Presente' : 'Mancante'}</dd></div><div><dt className="text-text-secondary">Testo</dt><dd>{file.has_lyrics ? 'Presente' : 'Mancante'}</dd></div></dl></section>
      </>}
      {showCandidates && <section className="rounded-md border border-border-subtle p-4"><div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">Corrispondenze</h2><p className="mt-1 text-sm text-text-secondary">La ricerca diretta è ora gestita dalla review: usa la vista review per cercare candidati.</p></div><Button size="sm" variant="ghost" onClick={() => setShowCandidates(false)}>Chiudi</Button></div><p className="mt-3 text-sm text-text-secondary">Apri una review per questo file e usa la ricerca manuale al suo interno.</p></section>}
      {showManual && <section className="rounded-md border border-border-subtle p-4"><h2 className="font-semibold">Modifica manuale</h2><p className="mt-1 text-sm text-text-secondary">La modifica viene proposta in una review: non scrive subito il file.</p><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-sm">Titolo<Input value={manualTitle} onChange={setManualTitle} placeholder={file.title ?? 'Titolo'} /></label><label className="text-sm">Artista<Input value={manualArtist} onChange={setManualArtist} placeholder={file.artist ?? 'Artista'} /></label></div><div className="mt-4 flex gap-2"><Button disabled={manualReview.isPending || (!manualTitle.trim() && !manualArtist.trim())} onClick={() => { const fields: Record<string, unknown> = {}; if (manualTitle.trim()) fields.title = manualTitle.trim(); if (manualArtist.trim()) fields.artist = manualArtist.trim(); manualReview.mutate(fields) }}>{manualReview.isPending ? 'Apertura…' : 'Apri review'}</Button><Button variant="ghost" onClick={() => setShowManual(false)}>Annulla</Button></div></section>}
    </main>
  </div>
}
