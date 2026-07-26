import { useState, type ReactNode, type MouseEventHandler } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive'
export type ButtonSize = 'sm' | 'md'

export interface ButtonProps {
  variant?: ButtonVariant
  size?: ButtonSize
  disabled?: boolean
  children?: ReactNode
  onClick?: MouseEventHandler<HTMLButtonElement>
  type?: 'button' | 'submit' | 'reset'
}

const SIZES: Record<ButtonSize, { padding: string; fontSize: string; lineHeight: string; gap: number }> = {
  sm: { padding: '3px 8px', fontSize: 'var(--text-xs-size)', lineHeight: 'var(--text-xs-line)', gap: 5 },
  md: { padding: '5px 12px', fontSize: 'var(--text-sm-size)', lineHeight: 'var(--text-sm-line)', gap: 6 },
}

export function Button({
  variant = 'primary',
  size = 'md',
  disabled = false,
  children,
  onClick,
  type = 'button',
}: ButtonProps) {
  const [hover, setHover] = useState(false)
  const [pressed, setPressed] = useState(false)
  const [focused, setFocused] = useState(false)
  const s = SIZES[size]

  let background = 'transparent'
  let color = 'var(--text-primary)'
  let border = '1px solid transparent'

  if (variant === 'primary') {
    background = pressed ? 'var(--accent-active)' : hover ? 'var(--accent-hover)' : 'var(--accent-solid)'
    color = 'var(--text-on-accent)'
  } else if (variant === 'secondary') {
    background = hover ? 'var(--bg-surface-hover)' : 'var(--bg-surface-raised)'
    color = 'var(--text-primary)'
    border = '1px solid var(--border-default)'
  } else if (variant === 'ghost') {
    background = hover ? 'var(--bg-surface-hover)' : 'transparent'
    color = 'var(--text-secondary)'
  } else if (variant === 'destructive') {
    background = hover ? 'var(--diff-removed-bg)' : 'transparent'
    color = 'var(--diff-removed)'
    border = '1px solid var(--diff-removed)'
  }

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false)
        setPressed(false)
      }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: s.gap,
        padding: s.padding,
        fontFamily: 'var(--font-sans)',
        fontSize: s.fontSize,
        lineHeight: s.lineHeight,
        fontWeight: 'var(--font-weight-medium)',
        borderRadius: 'var(--radius-md)',
        border,
        background,
        color,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        transition: 'background var(--transition-fast), border-color var(--transition-fast)',
        outline: 'none',
        boxShadow: focused && !disabled ? '0 0 0 2px var(--bg-canvas), 0 0 0 4px var(--focus-ring)' : 'none',
      }}
    >
      {children}
    </button>
  )
}
