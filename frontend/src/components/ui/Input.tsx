import { useState } from 'react'

export interface InputProps {
  value?: string
  placeholder?: string
  mono?: boolean
  error?: boolean
  disabled?: boolean
  onChange?: (value: string) => void
}

export function Input({
  value = '',
  placeholder = '',
  mono = false,
  error = false,
  disabled = false,
  onChange,
}: InputProps) {
  const [focused, setFocused] = useState(false)
  const borderColor = error
    ? 'var(--diff-removed)'
    : focused
      ? 'var(--accent-solid)'
      : 'var(--border-default)'
  return (
    <input
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange?.(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      style={{
        width: '100%',
        boxSizing: 'border-box',
        padding: '6px 10px',
        fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
        fontSize: 'var(--text-sm-size)',
        lineHeight: 'var(--text-sm-line)',
        color: disabled ? 'var(--text-disabled)' : 'var(--text-primary)',
        background: 'var(--bg-surface)',
        border: `1px solid ${borderColor}`,
        borderRadius: 'var(--radius-md)',
        outline: 'none',
        boxShadow: focused && !error ? '0 0 0 3px var(--accent-subtle-bg)' : 'none',
        transition: 'border-color var(--transition-fast), box-shadow var(--transition-fast)',
        cursor: disabled ? 'not-allowed' : 'text',
      }}
    />
  )
}
