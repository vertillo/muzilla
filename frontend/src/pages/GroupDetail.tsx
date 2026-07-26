import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Checkbox, ConfidenceBar, EmptyState, TableRow } from '@/components/ui'
import { useForceToSingleton, useGroup, usePinGroup, useSplitGroup } from '@/hooks/useGroups'
import { useTrackDetails } from '@/hooks/useTracks'

export function GroupDetail() {
  const { id } = useParams<{ id: string }>()
  const groupId = id ? Number(id) : NaN
  const navigate = useNavigate()

  const { data: group, isLoading } = useGroup(Number.isFinite(groupId) ? groupId : null)
  const { tracks } = useTrackDetails(group?.track_ids ?? [])
  const pinGroup = usePinGroup()
  const splitGroup = useSplitGroup()
  const forceToSingleton = useForceToSingleton()

  const [selected, setSelected] = useState<Set<number>>(new Set())

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
      <div style={{ padding: 'var(--space-5)', borderBottom: '1px solid var(--border-subtle)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h1 style={{ fontSize: 'var(--text-lg-size)', fontWeight: 'var(--font-weight-semibold)', margin: 0 }}>
            {group.album ?? '(untitled group)'}
          </h1>
          <Badge tone={group.kind === 'partial_album' ? 'conflict' : 'neutral'}>{group.kind}</Badge>
          {group.is_pinned && <Badge tone="unchanged">pinned</Badge>}
          <div style={{ marginLeft: 'auto' }}>
            <Button variant="ghost" size="sm" onClick={() => navigate('/groups')}>
              Back to groups
            </Button>
          </div>
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
      </div>

      <div>
        {tracks.map((t) => (
          <TableRow key={t.id}>
            <div style={{ width: 24 }}>
              <Checkbox checked={selected.has(t.id)} onChange={() => toggle(t.id)} />
            </div>
            <div style={{ flex: '2 1 0', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {t.title ?? t.filename}
            </div>
            <div style={{ width: 60, color: 'var(--text-secondary)' }}>{t.track_no ?? '—'}</div>
            <div style={{ marginLeft: 'auto' }}>
              <Button size="sm" variant="ghost" onClick={() => forceToSingleton.mutate(t.id)}>
                Force to singleton
              </Button>
            </div>
          </TableRow>
        ))}
      </div>
    </div>
  )
}
