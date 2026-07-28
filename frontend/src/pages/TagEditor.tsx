import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Badge, Button, Checkbox, EmptyState, Input } from '@/components/ui'
import { useBulkEditTracks, usePatchTrack, useStripTracks } from '@/hooks/useChangesets'
import { useFields } from '@/hooks/useFields'
import { useTrackDetails } from '@/hooks/useTracks'
import { previewFindReplace, applyFindReplace } from '@/lib/api'
import { commonValue, MULTIPLE_VALUES, type FieldValue } from '@/lib/tagEditor'
import type { FieldInfo } from '@/lib/types'

function parseIds(raw: string | null): number[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n))
}

function fieldsByCategory(fields: FieldInfo[]): Map<string, FieldInfo[]> {
  const map = new Map<string, FieldInfo[]>()
  for (const f of fields) {
    if (!f.editable) continue
    const list = map.get(f.category) ?? []
    list.push(f)
    map.set(f.category, list)
  }
  return map
}

export function TagEditor() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const ids = useMemo(() => parseIds(searchParams.get('ids')), [searchParams])

  const { data: fieldsData } = useFields()
  const { tracks, isLoading } = useTrackDetails(ids)
  const patchTrack = usePatchTrack()
  const bulkEdit = useBulkEditTracks()
  const stripTracks = useStripTracks()

  const [edited, setEdited] = useState<Record<string, FieldValue>>({})
  const [lastChangesetId, setLastChangesetId] = useState<number | null>(null)

  const [frField, setFrField] = useState('comment')
  const [frFind, setFrFind] = useState('')
  const [frReplace, setFrReplace] = useState('')
  const [frRegex, setFrRegex] = useState(false)
  const [frPreview, setFrPreview] = useState<{ track_id: number; old_value: string; new_value: string }[]>([])

  const isBulk = ids.length > 1
  const fields = fieldsData?.items ?? []
  const grouped = fieldsByCategory(fields)

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

  if (isLoading) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Loading tracks…" />
      </div>
    )
  }

  function valueFor(field: string): FieldValue {
    if (field in edited) return edited[field]
    return commonValue(tracks, field)
  }

  function setValue(field: string, value: FieldValue) {
    setEdited((prev) => ({ ...prev, [field]: value }))
  }

  async function save() {
    // Only send fields the user actually touched — a bulk editor must
    // never silently flatten <multiple values> fields it didn't edit
    // (docs/PLAN.md §9).
    const touchedFields = Object.entries(edited).filter(([, v]) => v !== MULTIPLE_VALUES)
    if (touchedFields.length === 0) return

    if (isBulk) {
      const result = await bulkEdit.mutateAsync({
        trackIds: ids,
        fields: touchedFields.map(([field, value]) => ({ field, new_value: value })),
      })
      setLastChangesetId(result.id)
    } else {
      const fieldValues = Object.fromEntries(touchedFields)
      const result = await patchTrack.mutateAsync({ trackId: ids[0], fields: fieldValues })
      setLastChangesetId(result.id)
    }
    setEdited({})
  }

  async function runFindReplacePreview() {
    if (!frFind) {
      setFrPreview([])
      return
    }
    try {
      const res = await previewFindReplace({
        track_ids: ids,
        field: frField,
        find: frFind,
        replace: frReplace,
        use_regex: frRegex,
      })
      setFrPreview(res.rows)
    } catch {
      setFrPreview([])
    }
  }

  async function applyFindReplaceNow() {
    const result = await applyFindReplace({
      track_ids: ids,
      field: frField,
      find: frFind,
      replace: frReplace,
      use_regex: frRegex,
    })
    setLastChangesetId(result.id)
    setFrPreview([])
    setFrFind('')
    setFrReplace('')
  }

  async function strip() {
    const result = await stripTracks.mutateAsync(ids)
    setLastChangesetId(result.id)
  }

  const saving = patchTrack.isPending || bulkEdit.isPending
  const hasEdits = Object.entries(edited).some(([, v]) => v !== MULTIPLE_VALUES)

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: 'var(--space-6)', fontFamily: 'var(--font-sans)', color: 'var(--text-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 'var(--space-5)' }}>
        <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          {isBulk ? `Bulk edit — ${ids.length} tracks` : (tracks[0]?.title ?? tracks[0]?.filename ?? 'Edit track')}
        </h1>
        <Button variant="ghost" size="sm" onClick={() => navigate('/catalog')}>
          Back to catalog
        </Button>
      </div>

      {lastChangesetId !== null && (
        // docs/PLAN.md §12e step 6.2: the wording was already right, the
        // emphasis was not — a neutral gray-bordered strip is easy to
        // miss, and a user can navigate away believing the edit is done.
        <div
          style={{
            marginBottom: 'var(--space-5)',
            padding: 'var(--space-4)',
            background: 'var(--accent-subtle-bg)',
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ fontSize: 'var(--text-sm-size)', color: 'var(--accent-text)' }}>
            Staged as changeset #{lastChangesetId} (draft) — nothing written to disk yet.
          </span>
          <Button size="md" onClick={() => navigate(`/changes/${lastChangesetId}`)}>
            Review & apply
          </Button>
        </div>
      )}

      {[...grouped.entries()].map(([category, categoryFields]) => (
        <section key={category} style={{ marginBottom: 'var(--space-6)' }}>
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
            {category}
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {categoryFields.map((f) => {
              const value = valueFor(f.name)
              const isMultiple = value === MULTIPLE_VALUES
              return (
                <div key={f.name} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <label style={{ width: 160, flexShrink: 0, fontSize: 'var(--text-sm-size)', color: 'var(--text-secondary)' }}>
                    {f.label}
                  </label>
                  <div style={{ flex: 1 }}>
                    {f.multi_valued ? (
                      <Input
                        value={isMultiple ? '' : ((value as string[] | null) ?? []).join(', ')}
                        placeholder={isMultiple ? '<multiple values>' : 'comma-separated'}
                        onChange={(v) => setValue(f.name, v.split(',').map((s) => s.trim()).filter(Boolean))}
                      />
                    ) : f.type === 'bool' ? (
                      <Checkbox
                        checked={isMultiple ? false : Boolean(value)}
                        label={isMultiple ? '<multiple values>' : undefined}
                        onChange={(checked) => setValue(f.name, checked)}
                      />
                    ) : (
                      <Input
                        value={isMultiple ? '' : value === null ? '' : String(value)}
                        placeholder={isMultiple ? '<multiple values>' : ''}
                        onChange={(v) => setValue(f.name, f.type === 'int' ? (v === '' ? null : Number(v)) : v)}
                      />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ))}

      <div style={{ display: 'flex', gap: 8, marginBottom: 'var(--space-8)' }}>
        <Button onClick={save} disabled={!hasEdits || saving}>
          {saving ? 'Saving…' : 'Save as draft changeset'}
        </Button>
        <Button variant="secondary" onClick={strip} disabled={stripTracks.isPending}>
          Strip default tags
        </Button>
      </div>

      <section>
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
          Find &amp; replace across selection
        </h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <div style={{ width: 140 }}>
            <Input value={frField} onChange={setFrField} placeholder="field name" />
          </div>
          <div style={{ flex: 1 }}>
            <Input value={frFind} onChange={(v) => { setFrFind(v); }} placeholder="find" />
          </div>
          <div style={{ flex: 1 }}>
            <Input value={frReplace} onChange={setFrReplace} placeholder="replace" />
          </div>
          <Checkbox checked={frRegex} label="regex" onChange={setFrRegex} />
          <Button size="sm" variant="secondary" onClick={runFindReplacePreview}>
            Preview
          </Button>
        </div>

        {frPreview.length > 0 && (
          <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)', padding: 'var(--space-3)', marginBottom: 8 }}>
            <div style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)', marginBottom: 6 }}>
              {frPreview.length} value(s) affected
            </div>
            {frPreview.slice(0, 10).map((row) => (
              <div key={row.track_id} style={{ fontSize: 'var(--text-sm-size)', fontFamily: 'var(--font-mono)', marginBottom: 2 }}>
                <Badge tone="removed">{row.old_value}</Badge> → <Badge tone="added">{row.new_value}</Badge>
              </div>
            ))}
            <Button size="sm" onClick={applyFindReplaceNow} disabled={frPreview.length === 0}>
              Stage as changeset
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
