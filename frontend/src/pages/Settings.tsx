import { useEffect, useState } from 'react'
import { Badge, Button, Checkbox, EmptyState, Input, Modal, SkeletonRows } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useFields } from '@/hooks/useFields'
import { useProviderStatus, useTestProviderConnection } from '@/hooks/useProviderStatus'
import {
  useSettings,
  useUpdateProviderSetting,
  useUpdateStripFields,
  useUpdateTemplates,
  usePreviewTemplate,
} from '@/hooks/useSettings'
import { useToasts } from '@/hooks/useToasts'
import { ApiError } from '@/lib/api'

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

function ProviderRow({ provider, enabled, tokenConfigured, requiresToken, status }: {
  provider: string
  enabled: boolean
  tokenConfigured: boolean
  requiresToken: boolean
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
              placeholder={tokenConfigured ? 'Token configured — enter a new value to replace' : 'No token configured'}
              value={tokenDraft}
              onChange={setTokenDraft}
            />
          </div>
          <Button size="sm" variant="secondary" disabled={!tokenDraft || update.isPending} onClick={saveToken}>
            Save token
          </Button>
          {tokenConfigured && <Badge tone="added">token set</Badge>}
          {tokenConfigured && (
            <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => setConfirmClear(true)}>
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

export function Settings() {
  const settings = useSettings()
  const fields = useFields()
  const updateStripFields = useUpdateStripFields()
  const providerStatus = useProviderStatus()
  const toasts = useToasts()
  const [stripSelection, setStripSelection] = useState<Set<string> | null>(null)

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

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-screen">
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
                status={providerStatus.data?.items.find((item) => item.provider === p.provider)}
              />
            ))}
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
            title="Matching weights"
            description="Not yet configurable from this screen — the matching engine's field weights are still fixed constants. See docs/PROGRESS.md for why this was scoped out of the Settings screen for now."
          >
            <span className="text-xs text-text-muted">Coming in a future release.</span>
          </Section>
        </>
      )}
    </div>
  )
}
