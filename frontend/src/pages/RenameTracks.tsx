import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Badge, Button, EmptyState, Input } from '@/components/ui'
import { usePreviewPaths, useRenamePaths } from '@/hooks/useRename'
import type { PathPreviewRow } from '@/lib/types'

function parseIds(raw: string | null): number[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n))
}

export function RenameTracks() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const ids = useMemo(() => parseIds(searchParams.get('ids')), [searchParams])

  const [template, setTemplate] = useState('')
  const [rows, setRows] = useState<PathPreviewRow[] | null>(null)
  const [lastChangesetId, setLastChangesetId] = useState<number | null>(null)

  const preview = usePreviewPaths()
  const rename = useRenamePaths()

  if (ids.length === 0) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState
          title="No tracks selected"
          description="Open this page from the catalog by selecting one or more tracks."
          action={<Button onClick={() => navigate('/catalog')}>Back to catalog</Button>}
        />
      </div>
    )
  }

  async function runPreview() {
    setLastChangesetId(null)
    try {
      const res = await preview.mutateAsync({
        track_ids: ids,
        template: template || undefined,
      })
      setRows(res.rows)
    } catch {
      setRows(null)
    }
  }

  async function stage() {
    const result = await rename.mutateAsync({
      track_ids: ids,
      template: template || undefined,
    })
    setLastChangesetId(result.id)
    setRows(null)
  }

  const hasErrors = rows?.some((r) => r.errors.length > 0) ?? false
  const hasCollisions = rows?.some((r) => r.is_collision) ?? false
  const canStage = rows !== null && rows.length > 0 && !hasErrors && !hasCollisions

  return (
    <div style={{ maxWidth: 820, margin: '0 auto', padding: 'var(--space-6)', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 'var(--space-5)' }}>
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          Rename {ids.length} track{ids.length === 1 ? '' : 's'}
        </h1>
        <Button variant="ghost" size="sm" onClick={() => navigate('/catalog')}>
          Back to catalog
        </Button>
      </div>

      {lastChangesetId !== null && (
        <div
          style={{
            marginBottom: 'var(--space-5)',
            padding: 'var(--space-4)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ fontSize: 'var(--text-sm-size)' }}>
            Staged as changeset #{lastChangesetId} (draft) — nothing moved on disk yet.
          </span>
          <Button size="sm" onClick={() => navigate(`/changes/${lastChangesetId}`)}>
            Review & apply
          </Button>
        </div>
      )}

      <section style={{ marginBottom: 'var(--space-6)' }}>
        <h2
          style={{
            fontSize: 'var(--text-xs-size)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--tracking-wide)',
            color: 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
            marginBottom: 'var(--space-3)',
          }}
        >
          Path template
        </h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <div style={{ flex: 1 }}>
            <Input
              mono
              value={template}
              onChange={setTemplate}
              placeholder="leave blank to use the configured album/singleton template"
            />
          </div>
          <Button size="sm" variant="secondary" onClick={runPreview} disabled={preview.isPending}>
            {preview.isPending ? 'Rendering…' : 'Preview'}
          </Button>
        </div>
        <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
          e.g. <code>$albumartist - $album/$track $title</code> — nothing is moved until you stage and
          apply the resulting changeset.
        </div>
      </section>

      {rows !== null && (
        <section>
          <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', padding: 'var(--space-3)', marginBottom: 8 }}>
            <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)', marginBottom: 6 }}>
              {rows.length} track(s)
              {hasErrors && ' — some rows have errors'}
              {hasCollisions && ' — some rows collide'}
            </div>
            {rows.map((row) => (
              <div key={row.track_id} style={{ marginBottom: 6 }}>
                <div style={{ fontSize: 'var(--text-sm-size)', fontFamily: 'var(--font-mono)' }}>
                  <Badge tone="removed">{row.old_path}</Badge> →{' '}
                  <Badge tone={row.errors.length > 0 || row.is_collision ? 'conflict' : 'added'}>
                    {row.new_path}
                  </Badge>
                </div>
                {row.errors.map((err, i) => (
                  <div key={i} style={{ fontSize: 'var(--text-xs-size)', color: 'var(--diff-removed)' }}>
                    {err}
                  </div>
                ))}
                {row.is_collision && (
                  <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--diff-removed)' }}>
                    collides with another track&apos;s rendered path
                  </div>
                )}
              </div>
            ))}
            <Button size="sm" onClick={stage} disabled={!canStage || rename.isPending}>
              {rename.isPending ? 'Staging…' : 'Stage as changeset'}
            </Button>
          </div>
        </section>
      )}
    </div>
  )
}
