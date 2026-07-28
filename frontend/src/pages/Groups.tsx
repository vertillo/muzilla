import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, ConfidenceBar, EmptyState, TableRow } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useGroupList, useMergeGroups, usePinGroup, useRunCascade } from '@/hooks/useGroups'
import type { GroupSummary } from '@/lib/types'

const BASIS_LABEL: Record<string, string> = {
  release_id: 'Release ID',
  barcode: 'Barcode',
  catalog_label: 'Catalog/Label',
  tags: 'Tag match',
  singleton: 'Singleton',
  manual: 'Manual',
}

function confidencePercent(g: GroupSummary): number {
  return Math.round((g.grouping_confidence ?? 0) * 100)
}

export function Groups() {
  const { data, isLoading } = useGroupList()
  const runCascade = useRunCascade()
  const pinGroup = usePinGroup()
  const mergeGroups = useMergeGroups()
  const navigate = useNavigate()

  const [mergeSourceId, setMergeSourceId] = useState<number | null>(null);

  const groups = [...(data?.items ?? [])].sort((a, b) => confidencePercent(a) - confidencePercent(b))

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader
        title="Grouping workspace"
        actions={
          <Button size="sm" variant="secondary" disabled={runCascade.isPending} onClick={() => runCascade.mutate()}>
            {runCascade.isPending ? 'Running…' : 'Re-run grouping cascade'}
          </Button>
        }
      >
        <div style={{ marginTop: 6, fontSize: 'var(--text-xs-size)', color: 'var(--text-muted)' }}>
          Sorted worst-confidence first — these need attention.
        </div>
      </PageHeader>

      {mergeSourceId !== null && (
        <div
          style={{
            padding: 'var(--space-3) var(--space-5)',
            background: 'var(--accent-subtle-bg)',
            fontSize: 'var(--text-sm-size)',
          }}
        >
          Merging group #{mergeSourceId} — click "Merge into" on the destination group, or{' '}
          <button
            onClick={() => setMergeSourceId(null)}
            style={{ background: 'none', border: 'none', color: 'var(--accent-text)', cursor: 'pointer', padding: 0 }}
          >
            cancel
          </button>
          .
        </div>
      )}

      {isLoading ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState title="Loading groups…" />
        </div>
      ) : groups.length === 0 ? (
        <div style={{ padding: 'var(--space-9)' }}>
          <EmptyState
            title="No groups yet"
            description="Run the grouping cascade to infer albums and singletons from scanned tracks."
            action={<Button onClick={() => runCascade.mutate()}>Run grouping cascade</Button>}
          />
        </div>
      ) : (
        <div>
          {groups.map((g) => (
            <TableRow key={g.id}>
              <div style={{ width: 90 }}>
                <Badge tone={g.kind === 'partial_album' ? 'conflict' : 'neutral'}>{g.kind}</Badge>
              </div>
              <div style={{ width: 130 }}>
                <Badge tone="accent">{BASIS_LABEL[g.grouping_basis ?? ''] ?? g.grouping_basis ?? 'unknown'}</Badge>
              </div>
              <div style={{ width: 140 }}>
                <ConfidenceBar value={confidencePercent(g)} width={90} />
              </div>
              <div style={{ flex: '2 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {g.album ?? '(untitled)'}
              </div>
              <div style={{ flex: '1.5 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                {g.album_artist ?? '—'}
              </div>
              <div style={{ width: 110, color: 'var(--text-secondary)' }}>
                {g.expected_track_count ? `${g.track_count} of ${g.expected_track_count}` : g.track_count}
              </div>
              <div style={{ width: 24 }}>{g.is_pinned && <Badge tone="unchanged">pinned</Badge>}</div>
              <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                {mergeSourceId !== null && mergeSourceId !== g.id ? (
                  <Button
                    size="sm"
                    onClick={() => {
                      mergeGroups.mutate({ intoGroupId: g.id, fromGroupIds: [mergeSourceId] })
                      setMergeSourceId(null)
                    }}
                  >
                    Merge into
                  </Button>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => setMergeSourceId(g.id)}>
                    Merge…
                  </Button>
                )}
                {!g.is_pinned && (
                  <Button size="sm" variant="ghost" onClick={() => pinGroup.mutate(g.id)}>
                    Pin
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => navigate(`/groups/${g.id}`)}>
                  Detail
                </Button>
              </div>
            </TableRow>
          ))}
        </div>
      )}
    </div>
  )
}
