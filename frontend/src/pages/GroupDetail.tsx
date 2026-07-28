import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Checkbox, ConfidenceBar, EmptyState, Select, TableRow } from '@/components/ui'
import { PageHeader } from '@/components/PageHeader'
import {
  useForceToSingleton,
  useGroup,
  useGroupList,
  usePinGroup,
  useReassignTrack,
  useSplitGroup,
} from '@/hooks/useGroups'
import { useTrackDetails } from '@/hooks/useTracks'

function groupPickerLabel(g: { album_artist: string | null; album: string | null; id: number }): string {
  return `${g.album_artist ?? 'Unknown artist'} – ${g.album ?? '(untitled)'} (#${g.id})`
}

export function GroupDetail() {
  const { id } = useParams<{ id: string }>()
  const groupId = id ? Number(id) : NaN
  const navigate = useNavigate()

  const { data: group, isLoading } = useGroup(Number.isFinite(groupId) ? groupId : null)
  const { data: allGroups } = useGroupList()
  const { tracks } = useTrackDetails(group?.track_ids ?? [])
  const pinGroup = usePinGroup()
  const splitGroup = useSplitGroup()
  const forceToSingleton = useForceToSingleton()
  const reassignTrack = useReassignTrack()

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [moveTargets, setMoveTargets] = useState<Record<number, string>>({})

  if (isLoading) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Loading group…" />
      </div>
    )
  }

  if (!group) {
    return (
      <div style={{ padding: 'var(--space-9)' }}>
        <EmptyState title="Group not found" action={<Button onClick={() => navigate('/groups')}>Back to groups</Button>} />
      </div>
    )
  }

  function toggle(trackId: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
  }

  return (
    <div style={{ fontFamily: 'var(--font-sans)', color: 'var(--text-primary)', background: 'var(--bg-canvas)', minHeight: '100vh' }}>
      <PageHeader
        title={group.album ?? '(untitled group)'}
        breadcrumb={{ label: 'Groups', to: '/groups' }}
      >
        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Badge tone={group.kind === 'partial_album' ? 'conflict' : 'neutral'}>{group.kind}</Badge>
          {group.is_pinned && <Badge tone="unchanged">pinned</Badge>}
        </div>
        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 'var(--text-sm-size)', color: 'var(--text-secondary)' }}>
            {group.album_artist ?? 'Unknown artist'} · {group.track_count} track(s)
            {group.expected_track_count ? ` of ${group.expected_track_count}` : ''}
          </span>
          <ConfidenceBar value={Math.round((group.grouping_confidence ?? 0) * 100)} width={100} />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 'var(--space-4)' }}>
          {!group.is_pinned && (
            <Button size="sm" variant="secondary" onClick={() => pinGroup.mutate(group.id)}>
              Pin this grouping
            </Button>
          )}
          <Button
            size="sm"
            variant="secondary"
            disabled={selected.size === 0}
            onClick={() => {
              splitGroup.mutate({ groupId: group.id, trackIds: [...selected] })
              setSelected(new Set())
            }}
          >
            Split selected out ({selected.size})
          </Button>
        </div>
      </PageHeader>

      <div>
        {tracks.map((t) => {
          // "Drag tracks between groups" (docs/PLAN.md §9) implemented
          // as a click-based move-to-group picker rather than drag-and-
          // drop: the two are functionally equivalent (both call
          // reassign_track), and a picker is a much smaller diff than
          // wiring HTML5 drag-and-drop across two screens for a feature
          // this app doesn't use drag interactions for anywhere else.
          const otherGroups = (allGroups?.items ?? []).filter((g) => g.id !== group.id)
          const moveTarget = moveTargets[t.id] ?? ''
          return (
            <TableRow key={t.id}>
              <div style={{ width: 24 }}>
                <Checkbox checked={selected.has(t.id)} onChange={() => toggle(t.id)} />
              </div>
              <div style={{ flex: '2 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {t.title ?? t.filename}
              </div>
              <div style={{ width: 60, color: 'var(--text-secondary)' }}>{t.track_no ?? '—'}</div>
              <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                <div style={{ width: 220 }}>
                  <Select
                    value={moveTarget}
                    options={[
                      { value: '', label: 'Move to group…' },
                      ...otherGroups.map((g) => ({ value: String(g.id), label: groupPickerLabel(g) })),
                    ]}
                    onChange={(v) => setMoveTargets((prev) => ({ ...prev, [t.id]: v }))}
                  />
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!moveTarget || reassignTrack.isPending}
                  onClick={() => {
                    reassignTrack.mutate({ trackId: t.id, toGroupId: Number(moveTarget) })
                    setMoveTargets((prev) => ({ ...prev, [t.id]: '' }))
                  }}
                >
                  Move
                </Button>
                <Button size="sm" variant="ghost" onClick={() => forceToSingleton.mutate(t.id)}>
                  Force to singleton
                </Button>
              </div>
            </TableRow>
          )
        })}
      </div>
    </div>
  )
}
