import { useEffect, useRef, type ReactNode } from 'react'

export interface ModalProps {
  open?: boolean
  title?: ReactNode
  children?: ReactNode
  onClose?: () => void
  footer?: ReactNode
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({ open = true, title, children, onClose, footer }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  // ChangeSetReview.tsx and friends pass onClose as a fresh inline
  // arrow function on every render — a ref means the effect below
  // doesn't need onClose in its dependency array and so doesn't tear
  // down and re-run (re-focusing, restoring focus to "previously
  // focused") on every unrelated parent re-render while the modal is
  // still open.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // docs/PLAN.md §12e step 6.5 item 6: Modal.tsx characterized (step
  // 4.2's Modal.test.tsx, two failing tests left as the target) with
  // neither a focus trap nor an Escape handler. On open, move focus
  // into the dialog; Tab/Shift+Tab wrap between the first and last
  // focusable elements instead of escaping to the page behind the
  // overlay; Escape closes it, same as clicking the backdrop's close
  // button.
  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    if (!dialog) return

    const previouslyFocused = document.activeElement as HTMLElement | null
    const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))

    // Deferred to the next task, not focused synchronously within this
    // effect: a Modal very often opens *from* a keydown handler (e.g.
    // ChangeSetReview.tsx's Enter-opens-Apply-modal). Native <button>
    // elements fire a click on the keyup half of an Enter/Space press
    // when focused — moving focus onto the modal's close button while
    // that same keystroke's keyup is still in flight made the browser
    // "click" Close immediately after Apply opened it. setTimeout(0)
    // lets the triggering keystroke finish first.
    const focusTimer = setTimeout(() => {
      const first = focusables()[0]
      ;(first ?? dialog).focus()
    }, 0)

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onCloseRef.current?.()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) {
        e.preventDefault()
        return
      }
      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      if (e.shiftKey && document.activeElement === firstItem) {
        e.preventDefault()
        lastItem.focus()
      } else if (!e.shiftKey && document.activeElement === lastItem) {
        e.preventDefault()
        firstItem.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      clearTimeout(focusTimer)
      document.removeEventListener('keydown', onKeyDown)
      previouslyFocused?.focus()
    }
  }, [open])

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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
        style={{
          width: 380,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-modal)',
          overflow: 'hidden',
          outline: 'none',
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
              aria-label="Close"
              className="focus-ring"
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
