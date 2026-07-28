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
      className="inline-flex border border-border-default rounded-md overflow-hidden"
      style={{ opacity: disabled ? 0.5 : 1 }}
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
            className={`w-[28px] h-[26px] flex items-center justify-center border-none font-mono text-sm font-bold ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
            style={{
              borderRight: i < 2 ? '1px solid var(--border-default)' : 'none',
              background: active ? s.bg : 'var(--bg-surface)',
              color: active ? s.color : 'var(--text-muted)',
            }}
          >
            {s.glyph}
          </button>
        )
      })}
    </div>
  )
}
