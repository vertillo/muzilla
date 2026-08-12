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
      <div className="p-9">
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
    <div className="max-w-[820px] mx-auto p-6 font-sans text-text-primary">
      <div className="flex items-center gap-4 mb-5">
        <h1 className="text-lg font-semibold m-0">
          Rename {ids.length} track{ids.length === 1 ? '' : 's'}
        </h1>
        <Button variant="ghost" size="sm" onClick={() => navigate('/catalog')}>
          Back to catalog
        </Button>
      </div>

      {lastChangesetId !== null && (
        // docs/product-spec.md: same emphasis fix as TagEditor.tsx —
        // the wording was already right, a neutral gray strip was not
        // emphatic enough to stop someone navigating away believing the
        // rename already happened.
        <div className="mb-5 p-4 rounded-md flex items-center justify-between bg-accent-subtle">
          <span className="text-sm text-accent-text">
            Staged as changeset #{lastChangesetId} (draft) — nothing moved on disk yet.
          </span>
          <Button size="md" onClick={() => navigate(`/changes/${lastChangesetId}`)}>
            Review & apply
          </Button>
        </div>
      )}

      <section className="mb-6">
        <h2 className="text-xs uppercase tracking-wide text-text-muted font-mono mb-3">
          Path template
        </h2>
        <div className="flex gap-[6px] items-center mb-[6px]">
          <div className="flex-1">
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
        <div className="text-xs text-text-muted">
          e.g. <code>$albumartist - $album/$track $title</code> — nothing is moved until you stage and
          apply the resulting changeset.
        </div>
      </section>

      {rows !== null && (
        <section>
          <div className="border border-border-subtle rounded-md p-3 mb-[6px]">
            <div className="text-xs text-text-muted mb-[6px]">
              {rows.length} track(s)
              {hasErrors && ' — some rows have errors'}
              {hasCollisions && ' — some rows collide'}
            </div>
            {rows.map((row) => (
              <div key={row.track_id} className="mb-[6px]">
                <div className="text-sm font-mono">
                  <Badge tone="removed">{row.old_path}</Badge> →{' '}
                  <Badge tone={row.errors.length > 0 || row.is_collision ? 'conflict' : 'added'}>
                    {row.new_path}
                  </Badge>
                </div>
                {row.errors.map((err, i) => (
                  <div key={i} className="text-xs text-diff-removed">
                    {err}
                  </div>
                ))}
                {row.is_collision && (
                  <div className="text-xs text-diff-removed">
                    collides with another track&apos;s rendered path
                  </div>
                )}
              </div>
            ))}
          </div>
          {/* docs/product-spec.md: this sat below the
              preview list, off-screen with many tracks — sticky to the
              bottom of the viewport instead. */}
          <div className="sticky bottom-0 p-3 mb-[6px] bg-canvas border-t border-border-subtle">
            <Button size="sm" onClick={stage} disabled={!canStage || rename.isPending}>
              {rename.isPending ? 'Staging…' : 'Stage as changeset'}
            </Button>
          </div>
        </section>
      )}
    </div>
  )
}
