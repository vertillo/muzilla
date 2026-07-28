export type ToastTone = 'success' | 'error' | 'warning' | 'info'

export interface ToastProps {
  tone?: ToastTone
  title?: string
  description?: string
  onDismiss?: () => void
}

const TONES: Record<ToastTone, { color: string; bg: string; glyph: string }> = {
  success: { color: 'var(--diff-added)', bg: 'var(--diff-added-bg)', glyph: '✓' },
  error: { color: 'var(--diff-removed)', bg: 'var(--diff-removed-bg)', glyph: '✕' },
  warning: { color: 'var(--diff-conflict)', bg: 'var(--diff-conflict-bg)', glyph: '▲' },
  info: { color: 'var(--accent-text)', bg: 'var(--accent-subtle-bg)', glyph: 'i' },
}

export function Toast({ tone = 'info', title, description, onDismiss }: ToastProps) {
  const t = TONES[tone]
  return (
    <div
      className="flex gap-[10px] items-start w-[320px] p-4 bg-surface-raised border border-border-default rounded-lg"
      style={{ boxShadow: 'var(--shadow-md)' }}
    >
      <div
        className="w-[18px] h-[18px] rounded-full flex items-center justify-center text-[10px] font-bold font-mono shrink-0 mt-px"
        style={{ background: t.bg, color: t.color }}
      >
        {t.glyph}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-sans text-sm font-medium text-text-primary">{title}</div>
        {description && (
          <div className="font-sans text-xs text-text-secondary mt-1">{description}</div>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="bg-transparent border-none text-text-muted cursor-pointer text-base leading-none p-0"
        >
          &times;
        </button>
      )}
    </div>
  )
}
