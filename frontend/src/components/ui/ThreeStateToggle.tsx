export type ToggleValue = 'accept' | 'pending' | 'reject'

export interface ThreeStateToggleProps {
  value?: ToggleValue
  disabled?: boolean
  onChange?: (value: ToggleValue) => void
}

const STATES: { key: ToggleValue; glyph: string; color: string; bg: string }[] = [
  { key: 'reject', glyph: '✕', color: 'var(--diff-removed)', bg: 'var(--diff-removed-bg)' },
  { key: 'pending', glyph: '·', color: 'var(--text-muted)', bg: 'var(--bg-surface-raised)' },
  { key: 'accept', glyph: '✓', color: 'var(--diff-added)', bg: 'var(--diff-added-bg)' },
]

export function ThreeStateToggle({ value = 'pending', disabled = false, onChange }: ThreeStateToggleProps) {
  return (
    <div
      style={{
        display: 'inline-flex',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {STATES.map((s, i) => {
        const active = s.key === value
        return (
          <button
            key={s.key}
            type="button"
            disabled={disabled}
            onClick={() => onChange?.(s.key)}
            title={s.key}
            style={{
              width: 28,
              height: 26,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: 'none',
              borderRight: i < 2 ? '1px solid var(--border-default)' : 'none',
              background: active ? s.bg : 'var(--bg-surface)',
              color: active ? s.color : 'var(--text-muted)',
              fontSize: 13,
              fontWeight: 700,
              cursor: disabled ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {s.glyph}
          </button>
        )
      })}
    </div>
  )
}
