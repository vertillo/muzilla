import { useState } from 'react'
import { Badge, Button, ConfidenceBar, EmptyState, type BadgeTone } from '@/components/ui'
import { useCandidates, useStageMatch } from '@/hooks/useMatching'
import type { CandidateRow } from '@/lib/types'

// docs/product-spec.md: the right pane is a release-level candidate picker,
// never a per-field provenance panel — porting the Change Review
// prototype's visual language (source badges, confidence bars, a
// selected/winner card treatment) but restructured around one row per
// (source, release). Picking a row re-stages the ENTIRE changeset via
// POST .../stage; there is no per-field "use this" button and no
// winnerOverrides, both explicitly rejected (see docs/product-spec.md's
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

  if (scopeId === null || scopeType !== 'track') {
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
      <div className="font-mono text-2xs text-text-muted uppercase tracking-wide mb-3">
        Candidates
      </div>

      {data.auto_applicable && (
        <div className="text-xs mb-[10px] text-diff-added">
          Top candidate is confident enough to auto-apply.
        </div>
      )}
      {!data.auto_applicable && data.needs_confirmation && (
        <div className="text-xs mb-[10px] text-diff-conflict">
          Top candidate needs confirmation before applying.
        </div>
      )}

      <div className="flex flex-col gap-3">
        {data.candidates.map((c) => {
          const key = rowKey(c)
          const isCurrent = c.source === currentCandidateSource && c.ref_id === currentCandidateRef
          const confidencePct = Math.round((1 - c.adjusted_distance) * 100)
          const isPicking = pickingKey === key && stageMutation.isPending

          return (
            <div
              key={key}
              className="rounded-md py-[10px] px-4"
              style={{
                border: `1px solid ${isCurrent ? 'var(--accent-solid)' : 'var(--border-subtle)'}`,
                background: isCurrent ? 'var(--accent-subtle-bg)' : 'var(--bg-surface)',
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <Badge tone={provenanceTone(c.source)} dot>
                  {sourceLabel(c.source)}
                </Badge>
                <div className="flex items-center gap-2">
                  {isCurrent && (
                    <span className="font-mono text-[9px] tracking-wide text-accent-text">
                      CURRENT
                    </span>
                  )}
                  {c.is_duplicate_of.length > 0 && (
                    <span
                      title="Another row describes the same release"
                      className="font-mono text-[9px] text-text-muted border border-border-subtle rounded-full py-px px-[6px]"
                    >
                      duplicate alt.
                    </span>
                  )}
                </div>
              </div>

              <div className="font-mono text-xs text-text-primary mt-[6px] break-words">
                {c.album ?? '(untitled)'}
              </div>
              <div className="text-xs text-text-secondary mt-1">
                {[c.album_artist, c.year, c.label, c.catalog_number, c.track_count === null ? 'Track count unknown' : `${c.track_count} tracks`]
                  .filter(Boolean)
                  .join(' · ')}
              </div>

              {c.representative_title && (
                <div className="text-xs text-text-secondary mt-1 break-words">
                  {c.candidate_type === 'track' ? 'Track match' : 'Representative track'}: {c.representative_title}
                  {c.representative_artist ? ` — ${c.representative_artist}` : ''}
                </div>
              )}

              {c.corroborated_by.length > 0 && (
                <div className="text-2xs mt-2 text-diff-added">
                  Corroborated by {c.corroborated_by.map(sourceLabel).join(', ')}
                </div>
              )}

              <div className="flex items-center justify-between mt-3 gap-3">
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
