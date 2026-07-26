import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input } from '@/components/ui'
import { useStartImport } from '@/hooks/useImports'

export function ImportWizard() {
  const [libraryRoot, setLibraryRoot] = useState('')
  const startImport = useStartImport()
  const navigate = useNavigate()

  function handleStart() {
    if (!libraryRoot.trim()) return
    startImport.mutate(libraryRoot.trim(), {
      onSuccess: (session) => navigate(`/import/${session.id}`),
    })
  }

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: 'var(--space-5)',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          Import a library
        </h1>
        <div style={{ marginLeft: 'auto' }}>
          <Button size="sm" variant="ghost" onClick={() => navigate('/jobs')}>
            Jobs
          </Button>
        </div>
      </div>

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
        <Input
          value={libraryRoot}
          placeholder="/music"
          mono
          disabled={startImport.isPending}
          onChange={setLibraryRoot}
        />

        {startImport.isError && (
          <div style={{ marginTop: 8, color: 'var(--diff-removed)', fontSize: 'var(--text-sm-size)' }}>
            {(startImport.error as Error).message}
          </div>
        )}

        <div style={{ marginTop: 'var(--space-5)' }}>
          <Button
            variant="primary"
            disabled={!libraryRoot.trim() || startImport.isPending}
            onClick={handleStart}
          >
            {startImport.isPending ? 'Starting…' : 'Start import'}
          </Button>
        </div>
      </div>
    </div>
  )
}
