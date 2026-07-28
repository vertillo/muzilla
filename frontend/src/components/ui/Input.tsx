import { useState } from 'react'

export interface InputProps {
  value?: string
  placeholder?: string
  type?: 'text' | 'password' | 'search'
  mono?: boolean
  error?: boolean
  disabled?: boolean
  onChange?: (value: string) => void
}

export function Input({
  value = '',
  placeholder = '',
  type = 'text',
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
      type={type}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange?.(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className={`w-full box-border px-[10px] py-[6px] text-sm rounded-md outline-none bg-surface ${mono ? 'font-mono' : 'font-sans'} ${disabled ? 'cursor-not-allowed' : 'cursor-text'}`}
      style={{
        color: disabled ? 'var(--text-disabled)' : 'var(--text-primary)',
        border: `1px solid ${borderColor}`,
        boxShadow: focused && !error ? '0 0 0 3px var(--accent-subtle-bg)' : 'none',
        transition: 'border-color var(--transition-fast), box-shadow var(--transition-fast)',
      }}
    />
  )
}
