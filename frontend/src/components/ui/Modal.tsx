import type { ReactNode } from 'react'

export interface ModalProps {
  open?: boolean
  title?: ReactNode
  children?: ReactNode
  onClose?: () => void
  footer?: ReactNode
}

export function Modal({ open = true, title, children, onClose, footer }: ModalProps) {
  if (!open) return null
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        background: 'rgba(4,5,7,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 10,
      }}
    >
      <div
        style={{
          width: 380,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-modal)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: 'var(--space-5)',
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          <div
            style={{
              fontFamily: 'var(--font-sans)',
              fontSize: 'var(--text-md-size)',
              fontWeight: 'var(--font-weight-semibold)',
              color: 'var(--text-primary)',
            }}
          >
            {title}
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                fontSize: 16,
              }}
            >
              &times;
            </button>
          )}
        </div>
        <div
          style={{
            padding: 'var(--space-5)',
            fontFamily: 'var(--font-sans)',
            fontSize: 'var(--text-sm-size)',
            color: 'var(--text-secondary)',
          }}
        >
          {children}
        </div>
        {footer && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 8,
              padding: 'var(--space-4) var(--space-5)',
              borderTop: '1px solid var(--border-subtle)',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
