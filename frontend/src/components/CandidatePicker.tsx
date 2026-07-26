import { useState } from 'react'
import { Badge, Button, ConfidenceBar, EmptyState, type BadgeTone } from '@/components/ui'
import { useCandidates, useStageMatch } from '@/hooks/useMatching'
import type { CandidateRow } from '@/lib/types'

// docs/PLAN.md §9: the right pane is a release-level candidate picker,
// never a per-field provenance panel — porting the Change Review
// prototype's visual language (source badges, confidence bars, a
// selected/winner card treatment) but restructured around one row per
// (source, release). Picking a row re-stages the ENTIRE changeset via
// POST .../stage; there is no per-field "use this" button and no
// winnerOverrides, both explicitly rejected (see docs/PROGRESS.md's
// "one release, one source" section).

const KNOWN_PROVENANCE: Record<string, BadgeTone> = {
  musicbrainz: 'musicbrainz',
  discogs: 'discogs',
  deezer: 'deezer',
}

function provenanceTone(source: string): BadgeTone {
  return KNOWN_PROVENANCE[source] ?? 'neutral'
}

function sourceLabel(source: string): string {
  if (source === 'musicbrainz') return 'MusicBrainz'
  if (source === 'discogs') return 'Discogs'
  if (source === 'deezer') return 'Deezer'
  return source
}

interface CandidatePickerProps {
  scopeType: string
  scopeId: number | null
  /** The changeset's already-chosen candidate, if any — highlighted as
   * the current selection rather than merely the top-ranked row. */
  currentCandidateSource: string | null
  currentCandidateRef: string | null
  onStaged?: (changeSetId: number) => void
}

export function CandidatePicker({
  scopeType,
  scopeId,
  currentCandidateSource,
  currentCandidateRef,
  onStaged,
}: CandidatePickerProps) {
  const { data, isLoading } = useCandidates(scopeType, scopeId)
  const stageMutation = useStageMatch(scopeType, scopeId)
  const [pickingKey, setPickingKey] = useState<string | null>(null)

  if (scopeId === null || (scopeType !== 'group' && scopeType !== 'track')) {
    return (
      <EmptyState
        title="No candidates"
        description="Candidates are only available for changesets scoped to a group or track."
      />
    )
  }

  if (isLoading) {
    return <EmptyState title="Fetching candidates…" description="Querying enabled providers." />
  }

  if (!data || data.candidates.length === 0) {
    return (
      <EmptyState
        title="No candidates"
        description="No enabled provider returned a match for this release. Configure more providers in Settings, or adjust local tags and refresh."
      />
    )
  }

  const rowKey = (c: CandidateRow) => `${c.source}:${c.ref_id}`

  function handlePick(c: CandidateRow) {
    const key = rowKey(c)
    setPickingKey(key)
    stageMutation.mutate(
      { source: c.source, refId: c.ref_id },
      {
        onSuccess: (cs) => {
          setPickingKey(null)
          onStaged?.(cs.id)
        },
        onError: () => setPickingKey(null),
      },
    )
  }

  return (
    <div>
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-2xs-size)',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: 'var(--tracking-wide)',
          marginBottom: 8,
        }}
      >
        Candidates
      </div>

      {data.auto_applicable && (
        <div
          style={{
            fontSize: 'var(--text-xs-size)',
            color: 'var(--diff-added)',
            marginBottom: 10,
          }}
        >
          Top candidate is confident enough to auto-apply.
        </div>
      )}
      {!data.auto_applicable && data.needs_confirmation && (
        <div
          style={{
            fontSize: 'var(--text-xs-size)',
            color: 'var(--diff-conflict)',
            marginBottom: 10,
          }}
        >
          Top candidate needs confirmation before applying.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {data.candidates.map((c) => {
          const key = rowKey(c)
          const isCurrent = c.source === currentCandidateSource && c.ref_id === currentCandidateRef
          const confidencePct = Math.round((1 - c.adjusted_distance) * 100)
          const isPicking = pickingKey === key && stageMutation.isPending

          return (
            <div
              key={key}
              style={{
                border: `1px solid ${isCurrent ? 'var(--accent-solid)' : 'var(--border-subtle)'}`,
                background: isCurrent ? 'var(--accent-subtle-bg)' : 'var(--bg-surface)',
                borderRadius: 'var(--radius-md)',
                padding: '10px 12px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <Badge tone={provenanceTone(c.source)} dot>
                  {sourceLabel(c.source)}
                </Badge>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {isCurrent && (
                    <span
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 9,
                        color: 'var(--accent-text)',
                        letterSpacing: 'var(--tracking-wide)',
                      }}
                    >
                      CURRENT
                    </span>
                  )}
                  {c.is_duplicate_of.length > 0 && (
                    <span
                      title="Another row describes the same release"
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 9,
                        color: 'var(--text-muted)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: 'var(--radius-full)',
                        padding: '1px 6px',
                      }}
                    >
                      duplicate alt.
                    </span>
                  )}
                </div>
              </div>

              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--text-primary)',
                  marginTop: 6,
                  wordBreak: 'break-word',
                }}
              >
                {c.album ?? '(untitled)'}
              </div>
              <div
                style={{
                  fontSize: 'var(--text-xs-size)',
                  color: 'var(--text-secondary)',
                  marginTop: 2,
                }}
              >
                {[c.album_artist, c.year, c.label, c.catalog_number, `${c.track_count} tracks`]
                  .filter(Boolean)
                  .join(' · ')}
              </div>

              {c.corroborated_by.length > 0 && (
                <div
                  style={{
                    fontSize: 'var(--text-2xs-size)',
                    color: 'var(--diff-added)',
                    marginTop: 4,
                  }}
                >
                  Corroborated by {c.corroborated_by.map(sourceLabel).join(', ')}
                </div>
              )}

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8, gap: 8 }}>
                <ConfidenceBar value={confidencePct} width={100} />
                {!isCurrent && (
                  <Button size="sm" variant="secondary" disabled={isPicking} onClick={() => handlePick(c)}>
                    {isPicking ? 'Staging…' : 'Use this'}
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
