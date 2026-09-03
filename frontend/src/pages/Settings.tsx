import { useEffect, useState } from 'react'
import { Badge, Button, Checkbox, EmptyState, Input, Modal, SkeletonRows } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useFields } from '@/hooks/useFields'
import { useProviderStatus, useTestProviderConnection } from '@/hooks/useProviderStatus'
import {
  useFactoryReset,
  useResetMatching,
  useSettings,
  useUpdateEnrichment,
  useUpdateMatching,
  useUpdatePathsPolicy,
  useUpdateProviderSetting,
  useUpdateStripFields,
  useUpdateTemplates,
  usePreviewTemplate,
  useResetCatalogAndActivity,
} from '@/hooks/useSettings'
import { useCapabilities } from '@/hooks/useCapabilities'
import { useToasts } from '@/hooks/useToasts'
import { ApiError } from '@/lib/api'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'

const PROVIDER_LABELS: Record<string, string> = {
  musicbrainz: 'MusicBrainz',
  discogs: 'Discogs',
  deezer: 'Deezer',
  acoustid: 'AcoustID',
  coverartarchive: 'Cover Art Archive',
  lrclib: 'LRCLIB',
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-border-subtle p-5 flex flex-col gap-4">
      <div>
        <h2 className="text-md font-semibold m-0">{title}</h2>
        {description && <p className="text-xs text-text-muted mt-2 mb-0">{description}</p>}
      </div>
      {children}
    </section>
  )
}

function ProviderRow({ provider, enabled, tokenConfigured, requiresToken, externallyManaged, status }: {
  provider: string
  enabled: boolean
  tokenConfigured: boolean
  requiresToken: boolean
  externallyManaged?: boolean
  status?: { state: string; last_checked_at: string | null; last_error_detail: string | null }
}) {
  const [tokenDraft, setTokenDraft] = useState('')
  const [confirmClear, setConfirmClear] = useState(false)
  const update = useUpdateProviderSetting()
  const toasts = useToasts()
  const testConnection = useTestProviderConnection()

  function saveEnabled(next: boolean) {
    update.mutate(
      { provider, params: { enabled: next } },
      {
        onSuccess: () =>
          toasts.push({ tone: 'info', title: `${PROVIDER_LABELS[provider] ?? provider} ${next ? 'enabled' : 'disabled'}` }),
      },
    )
  }

  function saveToken() {
    if (!tokenDraft) return
    update.mutate(
      { provider, params: { token: tokenDraft } },
      {
        onSuccess: () => {
          setTokenDraft('')
          toasts.push({ tone: 'info', title: `${PROVIDER_LABELS[provider] ?? provider} token saved` })
        },
      },
    )
  }

  function test() {
    testConnection.mutate(provider, {
      onSuccess: (result) => toasts.push({
        tone: result.state === 'operational' ? 'info' : 'error',
        title: `${PROVIDER_LABELS[provider] ?? provider}: ${result.state.replaceAll('_', ' ')}`,
      }),
    })
  }

  function clearToken() {
    update.mutate(
      { provider, params: { token: '' } },
      {
        onSuccess: () => {
          setConfirmClear(false)
          toasts.push({ tone: 'info', title: `${PROVIDER_LABELS[provider] ?? provider} credential cleared` })
        },
      },
    )
  }

  return (
    <div className="flex items-center gap-4 py-3 border-b border-border-subtle">
      <div className="w-[160px]">
        <Checkbox checked={enabled} onChange={saveEnabled} label={PROVIDER_LABELS[provider] ?? provider} />
      </div>
      {requiresToken ? (
        <>
          <div className="w-[260px]">
            <Input
              type="password"
              placeholder={externallyManaged ? 'Externally managed — configured via Docker secret / env' : tokenConfigured ? 'Token configured — enter a new value to replace' : 'No token configured'}
              value={tokenDraft}
              onChange={setTokenDraft}
              disabled={!!externallyManaged}
            />
          </div>
          <Button size="sm" variant="secondary" disabled={!!externallyManaged || !tokenDraft || update.isPending} onClick={saveToken} title={externallyManaged ? 'Credential is externally managed and cannot be overwritten via UI' : undefined}>
            Save token
          </Button>
          {externallyManaged ? <Badge tone="neutral">Externally managed</Badge> : tokenConfigured && <Badge tone="added">token set</Badge>}
          {tokenConfigured && (
            <Button size="sm" variant="ghost" disabled={!!externallyManaged || update.isPending} onClick={() => setConfirmClear(true)} title={externallyManaged ? 'Credential is externally managed' : undefined}>
              Clear credential
            </Button>
          )}
        </>
      ) : (
        <span className="text-xs text-text-muted">No token required</span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <span className="text-xs text-text-muted">
          {status?.state.replaceAll('_', ' ') ?? 'checking'}
          {status?.last_checked_at ? ` · checked ${new Date(status.last_checked_at).toLocaleString()}` : ''}
        </span>
        <Button size="sm" variant="ghost" disabled={!enabled || status?.state === 'not_configured' || testConnection.isPending} onClick={test}>
          Test connection
        </Button>
      </div>
      {status?.last_error_detail && <span className="text-xs text-text-muted">{status.last_error_detail}</span>}
      <Modal
        open={confirmClear}
        title={`Clear ${PROVIDER_LABELS[provider] ?? provider} credential?`}
        onClose={() => setConfirmClear(false)}
        footer={(
          <>
            <Button variant="ghost" onClick={() => setConfirmClear(false)}>Cancel</Button>
            <Button variant="destructive" onClick={clearToken} disabled={update.isPending}>Clear credential</Button>
          </>
        )}
      >
        The saved credential will be removed. This cannot be undone.
      </Modal>
    </div>
  )
}

function TemplateField({ label, fieldKey, value }: { label: string; fieldKey: 'album' | 'singleton' | 'default'; value: string | null }) {
  const [draft, setDraft] = useState(value ?? '')
  const update = useUpdateTemplates()
  const preview = usePreviewTemplate()
  const toasts = useToasts()

  useEffect(() => {
    setDraft(value ?? '')
  }, [value])

  function save() {
    update.mutate(
      { [fieldKey]: draft },
      { onSuccess: () => toasts.push({ tone: 'info', title: `${label} template saved` }) },
    )
  }

  function runPreview() {
    preview.mutate(draft || '$artist - $title')
  }

  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs text-text-secondary font-mono">{label}</label>
      <div className="flex gap-2">
        <div className="flex-1">
          <Input mono value={draft} placeholder="e.g. $albumartist/$album/$track $title" onChange={setDraft} />
        </div>
        <Button size="sm" variant="ghost" onClick={runPreview} disabled={preview.isPending}>
          Preview
        </Button>
        <Button size="sm" variant="secondary" onClick={save} disabled={update.isPending}>
          Save
        </Button>
      </div>
      {preview.data && (
        <div
          className="font-mono text-xs py-2 px-3 rounded-sm bg-surface-raised"
          style={{ color: preview.data.errors.length > 0 ? 'var(--diff-removed)' : 'var(--text-secondary)' }}
        >
          {preview.data.errors.length > 0 ? preview.data.errors.join('; ') : preview.data.path}
        </div>
      )}
    </div>
  )
}

function EnrichmentControls({ enrichment }: { enrichment: { metadata_auto: boolean; art_auto: boolean; lyrics_auto: boolean; replaygain_auto: boolean } }) {
  const update = useUpdateEnrichment()
  const capabilities = useCapabilities()
  const toasts = useToasts()
  const replaygainState = capabilities.data?.replaygain.state
  const replaygainAvailable = capabilities.data?.replaygain.available
  const replaygainConstrained = enrichment.replaygain_auto && replaygainAvailable === false

  function toggle(field: 'metadata_auto' | 'art_auto' | 'lyrics_auto' | 'replaygain_auto', value: boolean) {
    update.mutate(
      { [field]: value },
      {
        onSuccess: () => toasts.push({ tone: 'info', title: `Enrichment ${field} ${value ? 'enabled' : 'disabled'}` }),
      },
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <Checkbox checked={enrichment.metadata_auto} onChange={(v) => toggle('metadata_auto', v)} label="Metadata auto (matching automatico)" />
      <span className="text-xs text-text-muted -mt-2">Se disabilitato, il matching automatico non crea review; resta disponibile la ricerca manuale per bundle.</span>
      <Checkbox checked={enrichment.art_auto} onChange={(v) => toggle('art_auto', v)} label="Art auto (cover automatica)" />
      <Checkbox checked={enrichment.lyrics_auto} onChange={(v) => toggle('lyrics_auto', v)} label="Lyrics auto" />
      <div className="flex flex-col gap-1">
        <Checkbox checked={enrichment.replaygain_auto} onChange={(v) => toggle('replaygain_auto', v)} label="ReplayGain auto" />
        {replaygainConstrained && (
          <span className="text-xs text-diff-removed">ReplayGain non disponibile: {capabilities.data?.replaygain.detail ?? replaygainState} — l'auto verrà ignorato finché la capability non è available.</span>
        )}
        {replaygainState && <span className="text-xs text-text-muted">Capability: {replaygainState}{capabilities.data?.replaygain.detail ? ` · ${capabilities.data.replaygain.detail}` : ''}</span>}
      </div>
    </div>
  )
}

function PathsPolicySection({ policy }: { policy: { create_directories: boolean } }) {
  const update = useUpdatePathsPolicy()
  const toasts = useToasts()
  function toggle(value: boolean) {
    update.mutate(
      { create_directories: value },
      { onSuccess: () => toasts.push({ tone: 'info', title: `create_directories ${value ? 'enabled' : 'disabled'}` }) },
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <Checkbox checked={policy.create_directories} onChange={toggle} label="Create directories (permetti '/' nei template)" />
      <p className="text-xs text-text-muted m-0">Quando disabilitato (default, flat library), i template non possono contenere '/' e le collisioni sono controllate su tutta la libreria. Quando abilitato, la collisione è per-directory.</p>
      <p className="text-xs text-text-muted m-0">Policy di collisione: nessuna disambiguazione automatica — le collisioni bloccano l'intero ReviewBundle e richiedono correzione manuale dei metadata o del template prima di un nuovo preview/Apply.</p>
    </div>
  )
}

function PolicySummary({ enrichment, pathsPolicy }: { enrichment: { metadata_auto: boolean; art_auto: boolean; lyrics_auto: boolean; replaygain_auto: boolean }; pathsPolicy: { create_directories: boolean } }) {
  const capabilities = useCapabilities()
  return (
    <div className="text-xs text-text-secondary flex flex-col gap-2">
      <div>Effective automatic enrichment: metadata {enrichment.metadata_auto ? 'on' : 'off'} · art {enrichment.art_auto ? 'on' : 'off'} · lyrics {enrichment.lyrics_auto ? 'on' : 'off'} · replaygain {enrichment.replaygain_auto ? 'on' : 'off'}</div>
      <div>Filename policy: create_directories {pathsPolicy.create_directories ? 'enabled (foldered)' : 'disabled (flat)'} · collision mode: {pathsPolicy.create_directories ? 'per-directory' : 'flat – whole library'}</div>
      <div>ReplayGain capability: {capabilities.data?.replaygain.state ?? 'checking'}{capabilities.data?.replaygain.detail ? ` · ${capabilities.data.replaygain.detail}` : ''}</div>
      <div className="text-text-muted">Questi valori sono usati per i nuovi import e per le nuove review; le review già create mantengono le loro operazioni proposte.</div>
    </div>
  )
}

function AdvancedMatchingSection({ matching }: { matching?: import('@/lib/api').MatchingSettings | null }) {
  const update = useUpdateMatching()
  const reset = useResetMatching()
  const toasts = useToasts()
  const fallback: import('@/lib/api').MatchingSettings = {
    album_weights: { album: 3, album_artist: 3, tracks: 2, missing_tracks: 0.9, unmatched_tracks: 0.6, year: 0.5, media: 0.5, country: 0.5, label: 0.5, catalog_number: 0.5, album_id: 5, barcode: 2, source: 2 },
    singleton_weights: { title: 3, artist: 3, length: 2.5, isrc: 4, acoustid: 5 },
    track_weights: { title: 3, artist: 2, length: 2, index: 1, track_id: 5, isrc: 4 },
    album_strong_threshold: 0.10,
    album_reject_threshold: 0.45,
    singleton_strong_threshold: 0.06,
    singleton_reject_threshold: 0.45,
    min_gap: 0.03,
    provider_order: ['musicbrainz', 'discogs', 'deezer'],
    source_penalty: 0.02,
  }
  const effective = matching ?? fallback
  const [draft, setDraft] = useState<import('@/lib/api').MatchingSettings>(effective)
  useEffect(() => setDraft(matching ?? fallback), [matching])

  const valid = (() => {
    if (draft.album_strong_threshold < 0 || draft.album_strong_threshold > 1) return false
    if (draft.album_reject_threshold < 0 || draft.album_reject_threshold > 1) return false
    if (draft.singleton_strong_threshold < 0 || draft.singleton_strong_threshold > 1) return false
    if (draft.singleton_reject_threshold < 0 || draft.singleton_reject_threshold > 1) return false
    if (draft.album_strong_threshold >= draft.album_reject_threshold) return false
    if (draft.singleton_strong_threshold >= draft.singleton_reject_threshold) return false
    if (draft.min_gap < 0 || draft.min_gap > 0.5) return false
    if (draft.source_penalty < 0 || draft.source_penalty > 0.1) return false
    const weights = { ...draft.album_weights, ...draft.singleton_weights, ...draft.track_weights }
    for (const v of Object.values(weights)) if (v < 0 || v > 20) return false
    if (new Set(draft.provider_order).size !== draft.provider_order.length) return false
    return true
  })()

  function save() {
    if (!valid) return
    update.mutate(
      {
        album_weights: draft.album_weights,
        singleton_weights: draft.singleton_weights,
        track_weights: draft.track_weights,
        album_strong_threshold: draft.album_strong_threshold,
        album_reject_threshold: draft.album_reject_threshold,
        singleton_strong_threshold: draft.singleton_strong_threshold,
        singleton_reject_threshold: draft.singleton_reject_threshold,
        min_gap: draft.min_gap,
        provider_order: draft.provider_order,
        source_penalty: draft.source_penalty,
      },
      {
        onSuccess: () => toasts.push({ tone: 'info', title: 'Advanced matching saved' }),
        onError: (e) => toasts.push({ tone: 'error', title: e instanceof ApiError ? e.message : 'Save failed' }),
      },
    )
  }

  function doReset() {
    reset.mutate(undefined, {
      onSuccess: () => toasts.push({ tone: 'info', title: 'Matching reset to defaults' }),
      onError: (e) => toasts.push({ tone: 'error', title: e instanceof ApiError ? e.message : 'Reset failed' }),
    })
  }

  function moveProvider(index: number, dir: -1 | 1) {
    const next = [...draft.provider_order]
    const target = index + dir
    if (target < 0 || target >= next.length) return
    const tmp = next[index]
    next[index] = next[target]
    next[target] = tmp
    setDraft({ ...draft, provider_order: next })
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs">Album strong threshold (0–1)
          <Input type="number" step="0.01" min={0} max={1} value={String(draft.album_strong_threshold)} onChange={(v) => setDraft({ ...draft, album_strong_threshold: parseFloat(v) || 0 })} />
        </label>
        <label className="flex flex-col gap-1 text-xs">Album reject threshold (0–1)
          <Input type="number" step="0.01" min={0} max={1} value={String(draft.album_reject_threshold)} onChange={(v) => setDraft({ ...draft, album_reject_threshold: parseFloat(v) || 0 })} />
        </label>
        <label className="flex flex-col gap-1 text-xs">Singleton strong threshold (0–1)
          <Input type="number" step="0.01" min={0} max={1} value={String(draft.singleton_strong_threshold)} onChange={(v) => setDraft({ ...draft, singleton_strong_threshold: parseFloat(v) || 0 })} />
        </label>
        <label className="flex flex-col gap-1 text-xs">Singleton reject threshold (0–1)
          <Input type="number" step="0.01" min={0} max={1} value={String(draft.singleton_reject_threshold)} onChange={(v) => setDraft({ ...draft, singleton_reject_threshold: parseFloat(v) || 0 })} />
        </label>
        <label className="flex flex-col gap-1 text-xs">Min gap first/second (equivalence zone)
          <Input type="number" step="0.01" min={0} max={0.5} value={String(draft.min_gap)} onChange={(v) => setDraft({ ...draft, min_gap: parseFloat(v) || 0 })} />
        </label>
        <label className="flex flex-col gap-1 text-xs">Source penalty (tie-breaker)
          <Input type="number" step="0.01" min={0} max={0.1} value={String(draft.source_penalty)} onChange={(v) => setDraft({ ...draft, source_penalty: parseFloat(v) || 0 })} />
        </label>
      </div>
      {!valid && <p role="alert" className="text-xs text-diff-removed">Validazione: soglie coerenti (strong &lt; reject), pesi non-negativi ≤20, gap 0–0.5, provider unici.</p>}
      <p className="text-xs text-text-muted">Provider order è solo tie-breaker entro min_gap dalla migliore distanza; non può scavalcare un candidato materialmente migliore.</p>
      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium">Provider order (preferenza)</span>
        <div className="flex flex-col gap-1">
          {draft.provider_order.map((p, i) => (
            <div key={p} className="flex items-center gap-2">
              <span className="w-32 text-xs">{PROVIDER_LABELS[p] ?? p} — {i + 1}</span>
              <Button size="sm" variant="ghost" disabled={i === 0} onClick={() => moveProvider(i, -1)} aria-label={`Sposta ${p} su`}>&uarr;</Button>
              <Button size="sm" variant="ghost" disabled={i === draft.provider_order.length - 1} onClick={() => moveProvider(i, 1)} aria-label={`Sposta ${p} giù`}>&darr;</Button>
            </div>
          ))}
        </div>
      </div>
      <details className="rounded border border-border-subtle p-3">
        <summary className="cursor-pointer text-xs font-medium">Pesi segnali principali (album/singleton/track)</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {Object.entries(draft.album_weights).map(([k, v]) => (
            <label key={`album-${k}`} className="flex flex-col gap-1 text-xs">album.{k}
              <Input type="number" step="0.1" min={0} max={20} value={String(v)} onChange={(val) => setDraft({ ...draft, album_weights: { ...draft.album_weights, [k]: parseFloat(val) || 0 } })} />
            </label>
          ))}
          {Object.entries(draft.singleton_weights).map(([k, v]) => (
            <label key={`singleton-${k}`} className="flex flex-col gap-1 text-xs">singleton.{k}
              <Input type="number" step="0.1" min={0} max={20} value={String(v)} onChange={(val) => setDraft({ ...draft, singleton_weights: { ...draft.singleton_weights, [k]: parseFloat(val) || 0 } })} />
            </label>
          ))}
        </div>
      </details>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" disabled={!valid || update.isPending} onClick={save}>Save matching</Button>
        <Button size="sm" variant="ghost" disabled={reset.isPending} onClick={doReset}>Reset to defaults</Button>
      </div>
      {(update.isError || reset.isError) && <p role="alert" className="text-xs text-diff-removed">{(update.error as ApiError)?.message ?? (reset.error as ApiError)?.message ?? 'Errore'}</p>}
    </div>
  )
}

export function Settings() {
  const settings = useSettings()
  const fields = useFields()
  const updateStripFields = useUpdateStripFields()
  const providerStatus = useProviderStatus()
  const toasts = useToasts()
  const [stripSelection, setStripSelection] = useState<Set<string> | null>(null)
  const [resetDialog, setResetDialog] = useState<'catalog' | 'factory' | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [password, setPassword] = useState('')
  const [resetKey, setResetKey] = useState('')
  const resetCatalog = useResetCatalogAndActivity()
  const factoryReset = useFactoryReset()
  const navigate = useNavigate()
  const setAuthenticated = useAuthStore((state) => state.setAuthenticated)

  useEffect(() => {
    if (settings.data) setStripSelection(new Set(settings.data.strip_fields))
  }, [settings.data])

  function toggleStripField(name: string) {
    setStripSelection((prev) => {
      const next = new Set(prev ?? [])
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  function saveStripFields() {
    if (!stripSelection) return
    updateStripFields.mutate([...stripSelection], {
      onSuccess: () => toasts.push({ tone: 'info', title: 'Strip rules saved' }),
    })
  }

  function openResetDialog(scope: 'catalog' | 'factory') {
    setResetDialog(scope)
    setConfirmation('')
    setPassword('')
    setResetKey(crypto.randomUUID())
  }

  function closeResetDialog() {
    if (resetCatalog.isPending || factoryReset.isPending) return
    setResetDialog(null)
  }

  function runReset() {
    if (resetDialog === 'catalog' && confirmation === 'RESET CATALOG AND ACTIVITY') {
      resetCatalog.mutate(
        { confirmation, key: resetKey },
        {
          onSuccess: () => {
            setResetDialog(null)
            navigate('/', { replace: true })
          },
        },
      )
    }
    if (resetDialog === 'factory' && confirmation === 'FACTORY RESET MUZILLA' && password) {
      factoryReset.mutate(
        { password, key: resetKey },
        {
          onSuccess: () => {
            setAuthenticated(false, true)
            setResetDialog(null)
            navigate('/login', { replace: true })
          },
        },
      )
    }
  }

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader title="Settings" />

      {settings.isError ? (
        <div className="p-9">
          <EmptyState
            title="Couldn't load settings"
            description={settings.error instanceof ApiError ? settings.error.message : 'The server returned an error.'}
          />
        </div>
      ) : settings.isLoading || !settings.data ? (
        <SkeletonRows count={6} />
      ) : (
        <>
          <Section
            title="Providers"
            description="Enable or disable metadata providers and configure their API tokens. Changes apply immediately; test a connection to refresh its diagnostic."
          >
            {settings.data.providers.map((p) => (
              <ProviderRow
                key={p.provider}
                provider={p.provider}
                enabled={p.enabled}
                tokenConfigured={p.token_configured}
                requiresToken={p.provider === 'discogs' || p.provider === 'acoustid'}
                externallyManaged={p.externally_managed}
                status={providerStatus.data?.items.find((item) => item.provider === p.provider)}
              />
            ))}
          </Section>

          <Section
            title="Automatic enrichment"
            description="Controlla quali arricchimenti vengono proposti automaticamente dopo il matching. Ha effetto immediato sui nuovi lavori; la ricerca manuale resta sempre disponibile."
          >
            <EnrichmentControls enrichment={settings.data.enrichment} />
          </Section>

          <Section
            title="Filename / collision"
            description="Collisioni di destinazione bloccano l'intero ReviewBundle: nessun file viene scritto finché non cambi i metadata o il template. Nessuna disambiguazione automatica."
          >
            <PathsPolicySection policy={settings.data.paths_policy} />
          </Section>

          <Section
            title="Filename templates"
            description="Override the default rename templates. Leave blank to use the packaged default. Preview renders against a sample track (Sigur Rós — Ágætis byrjun)."
          >
            <TemplateField label="Album tracks" fieldKey="album" value={settings.data.templates.album} />
            <TemplateField label="Singleton tracks" fieldKey="singleton" value={settings.data.templates.singleton} />
            <TemplateField label="Default (fallback)" fieldKey="default" value={settings.data.templates.default} />
          </Section>

          <Section
            title="Strip rules"
            description="Fields cleared by default when you run 'Strip tags' on a selection."
          >
            {fields.isLoading || !fields.data ? (
              <SkeletonRows count={3} />
            ) : (
              <>
                <div className="flex flex-wrap gap-3">
                  {fields.data.items
                    .filter((f) => f.editable)
                    .map((f) => (
                      <Checkbox
                        key={f.name}
                        checked={stripSelection?.has(f.name) ?? false}
                        onChange={() => toggleStripField(f.name)}
                        label={f.label}
                      />
                    ))}
                </div>
                <div>
                  <Button size="sm" variant="secondary" onClick={saveStripFields} disabled={updateStripFields.isPending}>
                    Save strip rules
                  </Button>
                </div>
              </>
            )}
          </Section>

          <Section
            title="Advanced matching"
            description="Pesi dei segnali principali, soglie strong/ambiguous/reject, gap minimo tra primo/secondo candidato e ordine provider (solo tie-breaker entro la zona di equivalenza). Validazione: non-negativi, soglie coerenti (strong < reject), provider unico. Reset riporta ai default sicuri. I punteggi raw sono visibili in Review."
          >
            <AdvancedMatchingSection matching={settings.data.matching} />
          </Section>

          <Section
            title="Effective policy"
            description="Valori effettivi usati per i nuovi import e review, inclusi i vincoli di capacità."
          >
            <PolicySummary enrichment={settings.data.enrichment} pathsPolicy={settings.data.paths_policy} />
          </Section>

          <Section
            title="Danger zone"
            description="Reset removes Muzilla metadata and activity, never music files or operator-owned backups. Each action states its exact scope."
          >
            <div className="flex flex-col gap-4">
              <div className="flex items-start justify-between gap-4 border border-border-default rounded-md p-4">
                <div>
                  <div className="text-sm font-semibold">Reset catalog and activity</div>
                  <p className="text-xs text-text-muted mb-0">
                    Removes tracks from the index, reviews, jobs, cache and managed blobs. Preserves all Settings and provider credentials.
                  </p>
                </div>
                <Button variant="destructive" onClick={() => openResetDialog('catalog')}>Reset catalog</Button>
              </div>
              <div className="flex items-start justify-between gap-4 border border-border-default rounded-md p-4">
                <div>
                  <div className="text-sm font-semibold">Factory reset</div>
                  <p className="text-xs text-text-muted mb-0">
                    Also removes Settings overrides and managed provider credentials, revokes every session and requires sign-in again. Bootstrap env/files and backups are preserved.
                  </p>
                </div>
                <Button variant="destructive" onClick={() => openResetDialog('factory')}>Factory reset</Button>
              </div>
            </div>
          </Section>

          <Modal
            open={resetDialog !== null}
            title={resetDialog === 'factory' ? 'Factory reset Muzilla?' : 'Reset catalog and activity?'}
            onClose={closeResetDialog}
            footer={(
              <>
                <Button variant="ghost" onClick={closeResetDialog}>Cancel</Button>
                <Button
                  variant="destructive"
                  disabled={
                    resetCatalog.isPending
                    || factoryReset.isPending
                    || (resetDialog === 'catalog' && confirmation !== 'RESET CATALOG AND ACTIVITY')
                    || (resetDialog === 'factory' && (confirmation !== 'FACTORY RESET MUZILLA' || !password))
                  }
                  onClick={runReset}
                >
                  {resetCatalog.isPending || factoryReset.isPending ? 'Resetting…' : 'Confirm reset'}
                </Button>
              </>
            )}
          >
            <div className="flex flex-col gap-4">
              <p className="m-0">
                Music files and backups are outside this operation and will not be changed.
              </p>
              <p className="m-0">
                {resetDialog === 'factory'
                  ? 'This scope also removes Settings overrides and managed provider credentials, then revokes every session.'
                  : 'Preserves all Settings and provider credentials.'}
              </p>
              <label className="flex flex-col gap-2 text-xs">
                Type <strong>{resetDialog === 'factory' ? 'FACTORY RESET MUZILLA' : 'RESET CATALOG AND ACTIVITY'}</strong>
                <Input value={confirmation} onChange={setConfirmation} />
              </label>
              {resetDialog === 'factory' && (
                <label className="flex flex-col gap-2 text-xs">
                  Current password
                  <Input type="password" value={password} onChange={setPassword} />
                </label>
              )}
              {(resetCatalog.error || factoryReset.error) && (
                <div role="alert" className="text-xs text-danger">
                  {(resetCatalog.error ?? factoryReset.error) instanceof ApiError
                    ? (resetCatalog.error ?? factoryReset.error)?.message
                    : 'Reset could not be completed. Retry with the same dialog.'}
                </div>
              )}
            </div>
          </Modal>
        </>
      )}
    </div>
  )
}
