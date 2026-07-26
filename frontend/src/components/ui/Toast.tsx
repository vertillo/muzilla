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
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        width: 320,
        padding: 'var(--space-4)',
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-md)',
      }}
    >
      <div
        style={{
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: t.bg,
          color: t.color,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 10,
          fontWeight: 700,
          fontFamily: 'var(--font-mono)',
          flexShrink: 0,
          marginTop: 1,
        }}
      >
        {t.glyph}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 'var(--text-sm-size)',
            fontWeight: 'var(--font-weight-medium)',
            color: 'var(--text-primary)',
          }}
        >
          {title}
        </div>
        {description && (
          <div
            style={{
              fontFamily: 'var(--font-sans)',
              fontSize: 'var(--text-xs-size)',
              color: 'var(--text-secondary)',
              marginTop: 2,
            }}
          >
            {description}
          </div>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontSize: 14,
            lineHeight: 1,
            padding: 0,
          }}
        >
          &times;
        </button>
      )}
    </div>
  )
}
