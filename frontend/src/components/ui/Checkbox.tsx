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
      onClick={() => !disabled && onChange?.(!checked)}
      className={`inline-flex items-center gap-3 font-sans text-sm text-text-primary ${disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
    >
      <span
        className="w-4 h-4 rounded-sm shrink-0 flex items-center justify-center"
        style={{
          background: active ? 'var(--accent-solid)' : 'var(--bg-surface)',
          border: `1px solid ${active ? 'var(--accent-solid)' : 'var(--border-default)'}`,
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
        {indeterminate && <span className="w-2 h-[2px] rounded-[1px]" style={{ background: 'var(--text-on-accent)' }} />}
      </span>
      {label && <span>{label}</span>}
    </label>
  )
}
