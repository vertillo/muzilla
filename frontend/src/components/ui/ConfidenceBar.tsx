export interface ConfidenceBarProps {
  /** 0-100 */
  value?: number
  width?: number
}

function toneFor(value: number): { fill: string; label: string } {
  if (value >= 80) return { fill: 'var(--diff-added)', label: 'high' }
  if (value >= 50) return { fill: 'var(--diff-conflict)', label: 'medium' }
  return { fill: 'var(--diff-removed)', label: 'low' }
}

export function ConfidenceBar({ value = 0, width = 96 }: ConfidenceBarProps) {
  const clamped = Math.max(0, Math.min(100, value))
  const t = toneFor(clamped)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div
        style={{
          width,
          height: 6,
          borderRadius: 'var(--radius-full)',
          background: 'var(--bg-surface-raised)',
          overflow: 'hidden',
        }}
        aria-label={`confidence: ${t.label}`}
      >
        <div
          style={{
            width: `${clamped}%`,
            height: '100%',
            background: t.fill,
            borderRadius: 'var(--radius-full)',
          }}
        />
      </div>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-2xs-size)',
          color: 'var(--text-muted)',
          width: 32,
        }}
      >
        {clamped}%
      </span>
    </div>
  )
}
