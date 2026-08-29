import { useNavigate } from 'react-router-dom'
import { Button, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useImportConfig, useStartImport } from '@/hooks/useImports'
import { useCapabilities } from '@/hooks/useCapabilities'
import { useSettings } from '@/hooks/useSettings'

function EffectivePolicyPreview() {
  const settings = useSettings()
  const caps = useCapabilities()
  if (!settings.data) return null
  const e = settings.data.enrichment
  const p = settings.data.paths_policy
  return (
    <div className="mt-5 p-3 rounded-md bg-surface-raised border border-border-subtle">
      <div className="text-xs font-semibold">Policy effettiva per nuovo import</div>
      <div className="text-xs text-text-secondary mt-1">
        Enrichment: metadata {e.metadata_auto ? 'on' : 'off'} · art {e.art_auto ? 'on' : 'off'} · lyrics {e.lyrics_auto ? 'on' : 'off'} · replaygain {e.replaygain_auto ? 'on' : 'off'} · collision: {p.create_directories ? 'per-directory' : 'flat (bloccante)'}
      </div>
      {caps.data && <div className="text-xs text-text-muted mt-1">ReplayGain: {caps.data.replaygain.state}{caps.data.replaygain.detail ? ` · ${caps.data.replaygain.detail}` : ''}</div>}
    </div>
  )
}

export function ImportWizard() {
  const { data: importConfig, isLoading: isConfigLoading } = useImportConfig()
  const startImport = useStartImport()
  const navigate = useNavigate()

  function handleStart() {
    const libraryRoot = importConfig?.library_root
    if (!libraryRoot || !importConfig.library_root_exists) return
    startImport.mutate(libraryRoot, {
      onSuccess: (session) => navigate(`/import/${session.id}`),
    })
  }

  // A free-text path input became wrong once scan/import roots were
  // constrained to
  // storage.library_root or a descendant — nothing else could ever be
  // typed here that would actually be accepted. Read-only display of
  // the configured root instead.
  const canStart = importConfig?.library_root_exists === true

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader title="Import a library" />

      <div className="max-w-[480px] mx-auto py-9 px-5">
        <p className="text-sm text-text-secondary mt-0">
          Scans the given folder, fingerprints and groups every track, then proposes matches from
          the configured providers. Nothing is written to disk until you review and apply each
          proposed changeset.
        </p>

        <label className="block text-xs text-text-muted mb-[6px]">Library root</label>

        {isConfigLoading ? (
          <EmptyState title="Loading configuration…" />
        ) : importConfig ? (
          <>
            <div className="py-3 px-4 rounded-md border border-border-default bg-surface-raised font-mono text-sm text-text-primary">
              {importConfig.library_root}
            </div>
            {!importConfig.library_root_exists && (
              <div className="mt-3 text-sm text-diff-removed">
                This path does not exist on disk. Set <code>MUZILLA_STORAGE__LIBRARY_ROOT</code> (or
                mount your library there) before starting an import.
              </div>
            )}
          </>
        ) : null}

        {startImport.isError && (
          <div className="mt-3 text-sm text-diff-removed">
            {(startImport.error as Error).message}
          </div>
        )}

        <div className="mt-5">
          <Button variant="primary" disabled={!canStart || startImport.isPending} onClick={handleStart}>
            {startImport.isPending ? 'Starting…' : 'Start import'}
          </Button>
        </div>
        <EffectivePolicyPreview />
      </div>
    </div>
  )
}
