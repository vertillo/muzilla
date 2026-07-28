import { useNavigate } from 'react-router-dom'
import { Button, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useImportConfig, useStartImport } from '@/hooks/useImports'

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

  // docs/PLAN.md §12e step 6.5 item 4: a free-text path input became
  // wrong once step 2.7 constrained scan/import roots to
  // storage.library_root or a descendant — nothing else could ever be
  // typed here that would actually be accepted. Read-only display of
  // the configured root instead.
  const canStart = importConfig?.library_root_exists === true

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader title="Import a library" />

      <div style={{ maxWidth: 480, margin: '0 auto', padding: 'var(--space-9) var(--space-5)' }}>
        <p style={{ fontSize: 'var(--text-sm-size)', color: 'var(--text-secondary)', marginTop: 0 }}>
          Scans the given folder, fingerprints and groups every track, then proposes matches from
          the configured providers. Nothing is written to disk until you review and apply each
          proposed changeset.
        </p>

        <label
          style={{
            display: 'block',
            fontSize: 'var(--text-xs-size)',
            color: 'var(--text-muted)',
            marginBottom: 6,
          }}
        >
          Library root
        </label>

        {isConfigLoading ? (
          <EmptyState title="Loading configuration…" />
        ) : importConfig ? (
          <>
            <div
              style={{
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-default)',
                background: 'var(--bg-surface-raised)',
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--text-sm-size)',
                color: 'var(--text-primary)',
              }}
            >
              {importConfig.library_root}
            </div>
            {!importConfig.library_root_exists && (
              <div style={{ marginTop: 8, color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
                This path does not exist on disk. Set <code>MUZILLA_STORAGE__LIBRARY_ROOT</code> (or
                mount your library there) before starting an import.
              </div>
            )}
          </>
        ) : null}

        {startImport.isError && (
          <div style={{ marginTop: 8, color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
            {(startImport.error as Error).message}
          </div>
        )}

        <div style={{ marginTop: 'var(--space-5)' }}>
          <Button variant="primary" disabled={!canStart || startImport.isPending} onClick={handleStart}>
            {startImport.isPending ? 'Starting…' : 'Start import'}
          </Button>
        </div>
      </div>
    </div>
  )
}
