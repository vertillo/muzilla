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
    <div className="flex items-center gap-3">
      <div
        className="rounded-full bg-surface-raised overflow-hidden h-[6px]"
        style={{ width }}
        aria-label={`confidence: ${t.label}`}
      >
        <div className="h-full rounded-full" style={{ width: `${clamped}%`, background: t.fill }} />
      </div>
      <span className="font-mono text-2xs text-text-muted w-8">{clamped}%</span>
    </div>
  )
}
