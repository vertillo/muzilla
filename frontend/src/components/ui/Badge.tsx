import type { ReactNode } from 'react'

export type BadgeTone =
  | 'added'
  | 'removed'
  | 'conflict'
  | 'unchanged'
  | 'musicbrainz'
  | 'discogs'
  | 'deezer'
  | 'accent'
  | 'neutral'

export interface BadgeProps {
  tone?: BadgeTone
  /** Small leading dot instead of relying on background tint alone. */
  dot?: boolean
  children?: ReactNode
}

const TONES: Record<BadgeTone, { color: string; bg: string }> = {
  added: { color: 'var(--diff-added)', bg: 'var(--diff-added-bg)' },
  removed: { color: 'var(--diff-removed)', bg: 'var(--diff-removed-bg)' },
  conflict: { color: 'var(--diff-conflict)', bg: 'var(--diff-conflict-bg)' },
  unchanged: { color: 'var(--diff-unchanged)', bg: 'var(--diff-unchanged-bg)' },
  musicbrainz: { color: 'var(--provenance-musicbrainz)', bg: 'var(--provenance-musicbrainz-bg)' },
  discogs: { color: 'var(--provenance-discogs)', bg: 'var(--provenance-discogs-bg)' },
  deezer: { color: 'var(--provenance-deezer)', bg: 'var(--provenance-deezer-bg)' },
  accent: { color: 'var(--accent-text)', bg: 'var(--accent-subtle-bg)' },
  neutral: { color: 'var(--text-secondary)', bg: 'var(--bg-surface-raised)' },
}

export function Badge({ tone = 'neutral', dot = false, children }: BadgeProps) {
  const t = TONES[tone]
  return (
    <span
      className="inline-flex items-center gap-[5px] rounded-full px-3 py-1 font-mono text-2xs font-medium tracking-wide whitespace-nowrap"
      style={{ background: t.bg, color: t.color }}
    >
      {dot && <span className="w-[5px] h-[5px] rounded-full shrink-0" style={{ background: t.color }} />}
      {children}
    </span>
  )
}
