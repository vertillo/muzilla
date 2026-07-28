import { useEffect, useState } from 'react'
import { Badge, Button, Checkbox, EmptyState, Input, SkeletonRows } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useFields } from '@/hooks/useFields'
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
    <section
      style={{
        borderBottom: '1px solid var(--border-subtle)',
        padding: 'var(--space-5)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-4)',
      }}
    >
      <div>
        <h2 style={{ fontSize: 'var(--text-md-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
          {title}
        </h2>
        {description && (
          <p style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  )
}

function ProviderRow({ provider, enabled, tokenConfigured, requiresToken }: {
  provider: string
  enabled: boolean
  tokenConfigured: boolean
  requiresToken: boolean
}) {
  const [tokenDraft, setTokenDraft] = useState('')
  const update = useUpdateProviderSetting()
  const toasts = useToasts()

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

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-4)',
        padding: 'var(--space-3) 0',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ width: 160 }}>
        <Checkbox checked={enabled} onChange={saveEnabled} label={PROVIDER_LABELS[provider] ?? provider} />
      </div>
      {requiresToken ? (
        <>
          <div style={{ width: 260 }}>
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
        </>
      ) : (
        <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>No token required</span>
      )}
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <label style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
        {label}
      </label>
      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
        <div style={{ flex: 1 }}>
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
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-xs-size)',
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-surface-raised)',
            color: preview.data.errors.length > 0 ? 'var(--diff-removed)' : 'var(--text-secondary)',
          }}
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
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader title="Settings" />

      {settings.isError ? (
        <div style={{ padding: 'var(--space-9)' }}>
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
            description="Enable or disable metadata providers and configure their API tokens. Enabled/token changes take effect after a restart — templates and strip rules below take effect immediately."
          >
            {settings.data.providers.map((p) => (
              <ProviderRow
                key={p.provider}
                provider={p.provider}
                enabled={p.enabled}
                tokenConfigured={p.token_configured}
                requiresToken={p.provider === 'discogs' || p.provider === 'acoustid'}
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
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
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
            <span style={{ fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
              Coming in a future release.
            </span>
          </Section>
        </>
      )}
    </div>
  )
}
