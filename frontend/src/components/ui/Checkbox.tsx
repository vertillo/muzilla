export interface CheckboxProps {
  checked?: boolean
  indeterminate?: boolean
  disabled?: boolean
  label?: string
  onChange?: (checked: boolean) => void
}

export function Checkbox({
  checked = false,
  indeterminate = false,
  disabled = false,
  label,
  onChange,
}: CheckboxProps) {
  const active = checked || indeterminate
  return (
    <label
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontFamily: 'var(--font-sans)',
        fontSize: 'var(--text-sm-size)',
        color: 'var(--text-primary)',
      }}
    >
      <span
        onClick={() => !disabled && onChange?.(!checked)}
        style={{
          width: 16,
          height: 16,
          borderRadius: 'var(--radius-sm)',
          flexShrink: 0,
          background: active ? 'var(--accent-solid)' : 'var(--bg-surface)',
          border: `1px solid ${active ? 'var(--accent-solid)' : 'var(--border-default)'}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {checked && !indeterminate && (
          <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
            <path
              d="M1 4L3.5 6.5L9 1"
              stroke="var(--text-on-accent)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
        {indeterminate && (
          <span style={{ width: 8, height: 2, background: 'var(--text-on-accent)', borderRadius: 1 }} />
        )}
      </span>
      {label && <span>{label}</span>}
    </label>
  )
}
