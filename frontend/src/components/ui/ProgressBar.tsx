export interface ProgressBarProps {
  value?: number
  label?: string
}

export function ProgressBar({ value = 0, label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div className="flex flex-col gap-[6px] w-full">
      {label && (
        <div className="flex justify-between font-mono text-2xs text-text-muted">
          <span>{label}</span>
          <span>{clamped}%</span>
        </div>
      )}
      <div className="h-[6px] rounded-full bg-surface-raised overflow-hidden">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${clamped}%`, transition: 'width var(--transition-base)' }}
        />
      </div>
    </div>
  )
}
