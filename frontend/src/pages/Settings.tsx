import { useEffect, useState } from 'react'
import { Badge, Button, Checkbox, EmptyState, Input, Modal, SkeletonRows } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useFields } from '@/hooks/useFields'
import { useProviderStatus, useTestProviderConnection } from '@/hooks/useProviderStatus'
import {
  useFactoryReset,
  useSettings,
  useUpdateProviderSetting,
  useUpdateStripFields,
  useUpdateTemplates,
  usePreviewTemplate,
  useResetCatalogAndActivity,
} from '@/hooks/useSettings'
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
            description="Matching weights are not currently user-configurable."
          >
            <span className="text-xs text-text-muted">The matching algorithm uses fixed internal weights.</span>
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
