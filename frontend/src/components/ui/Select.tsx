import { useState } from 'react'

export interface SelectOption {
  value: string
  label: string
}

export interface SelectProps {
  value?: string
  options?: SelectOption[]
  disabled?: boolean
  onChange?: (value: string) => void
}

export function Select({ value, options = [], disabled = false, onChange }: SelectProps) {
  const [focused, setFocused] = useState(false)
  return (
    <div style={{ position: 'relative' }}>
      <select
        value={value}
        disabled={disabled}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(e) => onChange?.(e.target.value)}
        style={{
          appearance: 'none',
          width: '100%',
          boxSizing: 'border-box',
          padding: '6px 28px 6px 10px',
          fontFamily: 'var(--font-sans)',
          fontSize: 'var(--text-sm-size)',
          lineHeight: 'var(--text-sm-line)',
          color: disabled ? 'var(--text-disabled)' : 'var(--text-primary)',
          background: 'var(--bg-surface)',
          border: `1px solid ${focused ? 'var(--accent-solid)' : 'var(--border-default)'}`,
          borderRadius: 'var(--radius-md)',
          outline: 'none',
          boxShadow: focused ? '0 0 0 3px var(--accent-subtle-bg)' : 'none',
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div
        style={{
          position: 'absolute',
          right: 10,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 0,
          height: 0,
          borderLeft: '4px solid transparent',
          borderRight: '4px solid transparent',
          borderTop: '5px solid var(--text-muted)',
          pointerEvents: 'none',
        }}
      />
    </div>
  )
}
