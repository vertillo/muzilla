import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Badge, Button, Checkbox, EmptyState, Input } from '@/components/ui'
import { usePatchTrack, useStripTracks } from '@/hooks/useChangesets'
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
  const stripTracks = useStripTracks()

  const [edited, setEdited] = useState<Record<string, FieldValue>>({})
  const [lastChangesetId, setLastChangesetId] = useState<number | null>(null)

  const [frField, setFrField] = useState('comment')
  const [frFind, setFrFind] = useState('')
  const [frReplace, setFrReplace] = useState('')
  const [frRegex, setFrRegex] = useState(false)
  const [frPreview, setFrPreview] = useState<{ track_id: number; old_value: string; new_value: string }[]>([])

  const fields = fieldsData?.items ?? []
  const grouped = fieldsByCategory(fields)

  if (ids.length !== 1) {
    return (
      <div className="p-9">
        <EmptyState
          title="Modifica disponibile per un solo file"
          description="Apri il dettaglio di un file dal catalogo e scegli “Modifica manualmente”."
          action={<Button onClick={() => navigate('/catalog')}>Back to catalog</Button>}
        />
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="p-9">
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
    // (docs/product-spec.md).
    const touchedFields = Object.entries(edited).filter(([, v]) => v !== MULTIPLE_VALUES)
    if (touchedFields.length === 0) return

    const fieldValues = Object.fromEntries(touchedFields)
    const result = await patchTrack.mutateAsync({ trackId: ids[0], fields: fieldValues })
    setLastChangesetId(result.id)
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

  const saving = patchTrack.isPending
  const hasEdits = Object.entries(edited).some(([, v]) => v !== MULTIPLE_VALUES)

  return (
    <div className="max-w-[720px] mx-auto p-6 font-sans text-text-primary">
      <div className="flex items-center gap-4 mb-5">
        <h1 className="text-lg font-semibold m-0">
          {tracks[0]?.title ?? tracks[0]?.filename ?? 'Modifica file'}
        </h1>
        <Button variant="ghost" size="sm" onClick={() => navigate('/catalog')}>
          Back to catalog
        </Button>
      </div>

      {lastChangesetId !== null && (
        // docs/product-spec.md: the wording was already right, the
        // emphasis was not — a neutral gray-bordered strip is easy to
        // miss, and a user can navigate away believing the edit is done.
        <div className="mb-5 p-4 rounded-md flex items-center justify-between bg-accent-subtle">
          <span className="text-sm text-accent-text">
            Staged as changeset #{lastChangesetId} (draft) — nothing written to disk yet.
          </span>
          <Button size="md" onClick={() => navigate(`/changes/${lastChangesetId}`)}>
            Review & apply
          </Button>
        </div>
      )}

      {[...grouped.entries()].map(([category, categoryFields]) => (
        <section key={category} className="mb-6">
          <h2 className="text-xs uppercase tracking-wide text-text-muted font-mono mb-3">
            {category}
          </h2>
          <div className="flex flex-col gap-3">
            {categoryFields.map((f) => {
              const value = valueFor(f.name)
              const isMultiple = value === MULTIPLE_VALUES
              return (
                <div key={f.name} className="flex items-center gap-4">
                  <label className="w-[160px] shrink-0 text-sm text-text-secondary">
                    {f.label}
                  </label>
                  <div className="flex-1">
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

      <div className="flex gap-[6px] mb-8">
        <Button onClick={save} disabled={!hasEdits || saving}>
          {saving ? 'Saving…' : 'Save as draft changeset'}
        </Button>
        <Button variant="secondary" onClick={strip} disabled={stripTracks.isPending}>
          Strip default tags
        </Button>
      </div>

      <section>
        <h2 className="text-xs uppercase tracking-wide text-text-muted font-mono mb-3">
          Find &amp; replace across selection
        </h2>
        <div className="flex gap-[6px] items-center mb-[6px]">
          <div className="w-[140px]">
            <Input value={frField} onChange={setFrField} placeholder="field name" />
          </div>
          <div className="flex-1">
            <Input value={frFind} onChange={(v) => { setFrFind(v); }} placeholder="find" />
          </div>
          <div className="flex-1">
            <Input value={frReplace} onChange={setFrReplace} placeholder="replace" />
          </div>
          <Checkbox checked={frRegex} label="regex" onChange={setFrRegex} />
          <Button size="sm" variant="secondary" onClick={runFindReplacePreview}>
            Preview
          </Button>
        </div>

        {frPreview.length > 0 && (
          <div className="border border-border-subtle rounded-md p-3 mb-[6px]">
            <div className="text-xs text-text-muted mb-[6px]">
              {frPreview.length} value(s) affected
            </div>
            {frPreview.slice(0, 10).map((row) => (
              <div key={row.track_id} className="text-sm font-mono mb-[2px]">
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
