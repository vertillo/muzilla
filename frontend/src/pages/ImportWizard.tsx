import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useCapabilities } from '@/hooks/useCapabilities'
import { useSettings } from '@/hooks/useSettings'
import { useImportBrowse, useImportConfig, useImportPreview, useStartImport } from '@/hooks/useImports'

function EffectivePolicyPreview({ supportedCount }: { supportedCount: number | null }) {
  const settings = useSettings()
  const caps = useCapabilities()
  if (!settings.data?.enrichment || !settings.data?.paths_policy) return null
  const e = settings.data.enrichment
  const p = settings.data.paths_policy
  const fingerprint = e.metadata_auto ? 'fingerprint on' : 'fingerprint off'
  const replay = e.replaygain_auto ? 'replaygain on' : 'replaygain off'
  return (
    <div className="mt-5 p-3 rounded-md bg-surface-raised border border-border-subtle">
      <div className="text-xs font-semibold">Policy effettiva per nuovo import</div>
      <div className="text-xs text-text-secondary mt-1">
        Enrichment: metadata {e.metadata_auto ? 'on' : 'off'} · art {e.art_auto ? 'on' : 'off'} · lyrics {e.lyrics_auto ? 'on' : 'off'} · replaygain {e.replaygain_auto ? 'on' : 'off'} · {fingerprint} · {replay} · collision: {p.create_directories ? 'per-directory' : 'flat (bloccante)'}
      </div>
      {supportedCount !== null && (
        <div className="text-xs text-text-muted mt-1">
          Costo stimato: {supportedCount} file audio da esaminare{supportedCount > 0 ? ` · ${e.metadata_auto ? 'matching attivo' : 'matching disattivato'} · art/lyrics ${e.art_auto || e.lyrics_auto ? 'secondo policy' : 'disattivati'}` : ''} · nessuna scrittura su disco prima di Apply.
        </div>
      )}
      {caps.data && <div className="text-xs text-text-muted mt-1">ReplayGain: {caps.data.replaygain.state}{caps.data.replaygain.detail ? ` · ${caps.data.replaygain.detail}` : ''}</div>}
    </div>
  )
}

export function ImportWizard() {
  const { data: importConfig, isLoading: isConfigLoading } = useImportConfig()
  const startImport = useStartImport()
  const navigate = useNavigate()

  const [currentPath, setCurrentPath] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  useEffect(() => {
    if (importConfig?.library_root && importConfig.library_root_exists && currentPath === null) {
      setCurrentPath(importConfig.library_root)
      setSelectedPath(importConfig.library_root)
    }
  }, [importConfig, currentPath])

  const browse = useImportBrowse(currentPath)
  const preview = useImportPreview(selectedPath)

  const canStartBase = importConfig?.library_root_exists === true && !!selectedPath && !preview.isLoading
  const previewSupported = preview.data?.supported_count ?? null
  const canStart = canStartBase && (previewSupported === null || previewSupported > 0) && !preview.isError

  function handleStart() {
    if (!selectedPath) return
    startImport.mutate(selectedPath, {
      onSuccess: (session) => navigate(`/import/${session.id}`),
    })
  }

  const browseError = browse.error as Error | null
  const previewError = preview.error as Error | null

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader title="Import a library" />

      <div className="max-w-[640px] mx-auto py-9 px-5">
        <p className="text-sm text-text-secondary mt-0">
          Seleziona un file o una cartella dentro la libreria montata sul server, verifica l&apos;anteprima del perimetro e i costi, poi avvia. Nessun upload dal client e nessuna scrittura su disco prima di Apply.
        </p>

        <label className="block text-xs text-text-muted mb-[6px] mt-4">Library root (server-side)</label>

        {isConfigLoading ? (
          <EmptyState title="Loading configuration…" />
        ) : importConfig ? (
          <>
            <div className="py-3 px-4 rounded-md border border-border-default bg-surface-raised font-mono text-sm text-text-primary">
              {importConfig.library_root}
            </div>
            {!importConfig.library_root_exists && (
              <div className="mt-3 text-sm text-diff-removed">
                This path does not exist on disk. Set <code>MUZILLA_STORAGE__LIBRARY_ROOT</code> (or mount your library there) before starting an import.
              </div>
            )}
          </>
        ) : null}

        {importConfig?.library_root_exists && (
          <>
            <div className="mt-6">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Sfoglia la libreria</h2>
                <span className="text-xs font-mono text-text-muted">{currentPath ?? importConfig.library_root}</span>
              </div>

              <div className="mt-2 flex items-center gap-2">
                <Button size="sm" variant="ghost" disabled={!browse.data?.parent} onClick={() => browse.data?.parent && setCurrentPath(browse.data.parent)}>
                  ↑ Livello superiore
                </Button>
                <Button size="sm" variant="secondary" disabled={!browse.data?.path} onClick={() => browse.data?.path && setSelectedPath(browse.data.path)}>
                  Seleziona cartella corrente
                </Button>
                {browse.data?.truncated && <span className="text-xs text-text-muted">Elenco troncato a 500 voci</span>}
              </div>

              <div className="mt-3 rounded-md border border-border-subtle bg-surface-raised">
                {browse.isLoading ? (
                  <div className="p-4 text-sm text-text-muted">Caricamento…</div>
                ) : browseError ? (
                  <div className="p-4 text-sm text-diff-removed">{browseError.message}</div>
                ) : browse.data ? (
                  browse.data.entries.length === 0 ? (
                    <div className="p-4 text-sm text-text-muted">Cartella vuota.</div>
                  ) : (
                    <ul className="divide-y divide-border-subtle">
                      {browse.data.entries.map((entry) => {
                        const isSelected = selectedPath === entry.path
                        const isBlocked = entry.blocked
                        const isIgnored = entry.ignored
                        return (
                          <li key={entry.path} className={`flex items-center gap-3 px-3 py-2 ${isSelected ? 'bg-surface-hover' : ''} ${isBlocked || isIgnored ? 'opacity-60' : ''}`}>
                            <span className="text-xs font-mono w-8 text-center">{entry.kind === 'dir' ? '📁' : '🎵'}</span>
                            <span className={`flex-1 min-w-0 truncate font-mono text-sm ${isBlocked ? 'line-through' : ''}`} title={entry.path}>
                              {entry.name}
                              {entry.is_symlink && <span className="ml-2 text-xs text-text-muted">↗ symlink{entry.symlink_target ? ` → ${entry.symlink_target}` : ''}</span>}
                              {entry.supported === false && entry.kind === 'file' && !entry.ignored && entry.is_symlink === false && <span className="ml-2 text-xs text-text-muted">(non supportato)</span>}
                              {entry.ignored && <span className="ml-2 text-xs text-text-muted">(ignorato)</span>}
                              {entry.blocked && <span className="ml-2 text-xs text-diff-removed">(bloccato)</span>}
                            </span>
                            {entry.kind === 'dir' && !isBlocked && (
                              <Button size="sm" variant="ghost" onClick={() => setCurrentPath(entry.path)}>
                                Apri
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant={isSelected ? 'secondary' : 'ghost'}
                              disabled={isBlocked}
                              onClick={() => setSelectedPath(entry.path)}
                            >
                              {isSelected ? 'Selezionato' : 'Seleziona'}
                            </Button>
                          </li>
                        )
                      })}
                    </ul>
                  )
                ) : null}
              </div>
            </div>

            <div className="mt-6 p-3 rounded-md border border-border-subtle bg-surface-raised">
              <div className="text-xs font-semibold">Anteprima perimetro selezionato</div>
              <div className="text-xs font-mono text-text-muted mt-1 break-all">{selectedPath ?? '— nessuna selezione —'}</div>
              {preview.isLoading && <div className="text-xs text-text-muted mt-2">Calcolo anteprima…</div>}
              {previewError && <div className="text-xs text-diff-removed mt-2" role="alert">{previewError.message}</div>}
              {preview.data && (
                <div className="text-xs text-text-secondary mt-2 space-y-1">
                  <div>
                    {preview.data.scope_kind === 'file' ? 'File singolo' : 'Cartella'} · supportati: <span className="font-semibold">{preview.data.supported_count}</span> · non supportati: {preview.data.unsupported_count} · sidecar ignorati: {preview.data.ignored_sidecar_count} · cartelle escluse: {preview.data.excluded_dir_count} · symlink esclusi: {preview.data.symlink_excluded_count} · totali considerati: {preview.data.total_files_considered}
                  </div>
                  {preview.data.unsupported_count > 0 && preview.data.unsupported_examples.length > 0 && (
                    <div className="text-text-muted">Esempi non supportati: {preview.data.unsupported_examples.join(', ')}</div>
                  )}
                  {preview.data.excluded_dir_count > 0 && preview.data.excluded_dir_examples.length > 0 && (
                    <div className="text-text-muted">Cartelle escluse: {preview.data.excluded_dir_examples.join(', ')}</div>
                  )}
                  {preview.data.truncated && <div className="text-amber-700">Anteprima troncata: perimetro molto grande, conteggi parziali. L&apos;import comunque esaminerà l&apos;intero perimetro con indicizzazione incrementale.</div>}
                  {preview.data.supported_count === 0 && preview.data.scope_kind === 'directory' && <div className="text-diff-removed">Nessun file audio supportato in questo perimetro — seleziona un&apos;altra cartella.</div>}
                  {preview.data.scope_kind === 'file' && preview.data.supported_count === 0 && <div className="text-diff-removed">File non supportato o ignorato — non verrà indicizzato.</div>}
                </div>
              )}
            </div>

            <EffectivePolicyPreview supportedCount={previewSupported} />
          </>
        )}

        {startImport.isError && (
          <div className="mt-3 text-sm text-diff-removed" role="alert">
            {(startImport.error as Error).message}
          </div>
        )}

        <div className="mt-5">
          <Button variant="primary" disabled={!canStart || startImport.isPending} onClick={handleStart}>
            {startImport.isPending ? 'Starting…' : 'Start import'}
          </Button>
          {selectedPath && preview.data?.supported_count === 0 && <span className="ml-3 text-xs text-text-muted">Seleziona un perimetro con file supportati.</span>}
        </div>
      </div>
    </div>
  )
}
