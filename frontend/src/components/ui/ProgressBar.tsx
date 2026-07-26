export interface ProgressBarProps {
  value?: number
  label?: string
}

export function ProgressBar({ value = 0, label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
      {label && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-2xs-size)',
            color: 'var(--text-muted)',
          }}
        >
          <span>{label}</span>
          <span>{clamped}%</span>
        </div>
      )}
      <div
        style={{
          height: 6,
          borderRadius: 'var(--radius-full)',
          background: 'var(--bg-surface-raised)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${clamped}%`,
            height: '100%',
            background: 'var(--accent-solid)',
            borderRadius: 'var(--radius-full)',
            transition: 'width var(--transition-base)',
          }}
        />
      </div>
    </div>
  )
}
