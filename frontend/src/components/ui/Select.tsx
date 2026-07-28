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
    <div className="relative">
      <select
        value={value}
        disabled={disabled}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(e) => onChange?.(e.target.value)}
        className={`appearance-none w-full box-border pt-[6px] pr-[28px] pb-[6px] pl-[10px] font-sans text-sm rounded-md outline-none bg-surface ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
        style={{
          color: disabled ? 'var(--text-disabled)' : 'var(--text-primary)',
          border: `1px solid ${focused ? 'var(--accent-solid)' : 'var(--border-default)'}`,
          boxShadow: focused ? '0 0 0 3px var(--accent-subtle-bg)' : 'none',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div
        className="absolute top-1/2 -translate-y-1/2 w-0 h-0 pointer-events-none right-[10px]"
        style={{
          borderLeft: '4px solid transparent',
          borderRight: '4px solid transparent',
          borderTop: '5px solid var(--text-muted)',
        }}
      />
    </div>
  )
}
