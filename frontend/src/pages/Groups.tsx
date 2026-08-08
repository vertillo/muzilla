import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, ConfidenceBar, EmptyState, Modal, SkeletonRows, TableRow } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import { useGroupList, useMergeGroups, usePinGroup, useRunCascade } from '@/hooks/useGroups'
import { ApiError } from '@/lib/api'
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

function groupLabel(g: GroupSummary): string {
  return `${g.album_artist ?? 'Unknown artist'} – ${g.album ?? '(untitled)'}`
}

export function Groups() {
  const { data, isLoading, isError, error, refetch } = useGroupList()
  const runCascade = useRunCascade()
  const pinGroup = usePinGroup()
  const mergeGroups = useMergeGroups()
  const navigate = useNavigate()

  const [mergeSourceId, setMergeSourceId] = useState<number | null>(null)
  const [mergeTargetId, setMergeTargetId] = useState<number | null>(null)

  const groups = [...(data?.items ?? [])].sort((a, b) => confidencePercent(a) - confidencePercent(b))

  // docs/PLAN.md §12e step 6.5 item 2: merge mode had a banner and a
  // text "cancel" link, but Escape did nothing — add the escape hatch
  // a modal-driven flow implies is standard.
  useEffect(() => {
    if (mergeSourceId === null) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setMergeSourceId(null)
        setMergeTargetId(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [mergeSourceId])

  const sourceGroup = groups.find((g) => g.id === mergeSourceId)
  const targetGroup = groups.find((g) => g.id === mergeTargetId)

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader
        title="Grouping workspace"
        actions={
          <Button size="sm" variant="secondary" disabled={runCascade.isPending} onClick={() => runCascade.mutate()}>
            {runCascade.isPending ? 'Running…' : 'Re-run grouping cascade'}
          </Button>
        }
      >
        <div className="mt-[6px] text-xs text-text-muted">
          Sorted worst-confidence first — these need attention.
        </div>
      </PageHeader>

      {mergeSourceId !== null && sourceGroup && (
        <div className="py-3 px-5 text-sm bg-accent-subtle">
          Merging "{groupLabel(sourceGroup)}" — click "Merge into" on the destination group, or
          press Escape, or{' '}
          <button
            onClick={() => setMergeSourceId(null)}
            className="bg-transparent border-none cursor-pointer p-0 text-accent-text"
          >
            cancel
          </button>
          .
        </div>
      )}

      {isError ? (
        <div className="p-9">
          <EmptyState
            title="Couldn't load groups"
            description={error instanceof ApiError ? error.message : 'The server returned an error.'}
            action={<Button onClick={() => refetch()}>Retry</Button>}
          />
        </div>
      ) : isLoading ? (
        <SkeletonRows />
      ) : groups.length === 0 ? (
        <div className="p-9">
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
              <div className="w-[90px]">
                <Badge tone={g.kind === 'partial_album' ? 'conflict' : 'neutral'}>{g.kind}</Badge>
              </div>
              <div className="w-[130px]">
                <Badge tone="accent">{BASIS_LABEL[g.grouping_basis ?? ''] ?? g.grouping_basis ?? 'unknown'}</Badge>
              </div>
              <div className="w-[140px]">
                <ConfidenceBar value={confidencePercent(g)} width={90} />
              </div>
              <div className="flex-[2_1_0%] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
                {g.album ?? '(untitled)'}
              </div>
              <div className="flex-[1.5_1_0%] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-secondary">
                {g.album_artist ?? '—'}
              </div>
              <div className="w-[110px] text-text-secondary">
                {g.expected_track_count ? `${g.track_count} of ${g.expected_track_count}` : g.track_count}
              </div>
              <div className="w-7">{g.is_pinned && <Badge tone="unchanged">pinned</Badge>}</div>
              <div className="flex gap-[6px] ml-auto">
                {mergeSourceId !== null && mergeSourceId !== g.id ? (
                  <Button size="sm" onClick={() => setMergeTargetId(g.id)}>
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

      {mergeTargetId !== null && sourceGroup && targetGroup && (
        // docs/PLAN.md §12e step 6.5 item 2: confirmation naming both
        // groups — merging was one click with no confirmation at all.
        <Modal
          title="Merge these groups?"
          onClose={() => setMergeTargetId(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setMergeTargetId(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={mergeGroups.isPending}
                onClick={() => {
                  mergeGroups.mutate({ intoGroupId: targetGroup.id, fromGroupIds: [sourceGroup.id] })
                  setMergeSourceId(null)
                  setMergeTargetId(null)
                }}
              >
                Merge
              </Button>
            </>
          }
        >
          <div>
            "{groupLabel(sourceGroup)}" will be merged into "{groupLabel(targetGroup)}". This
            applies immediately — there is no separate review step, but the resulting changeset
            can still be undone from the Changes screen.
          </div>
        </Modal>
      )}
    </div>
  )
}
