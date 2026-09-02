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
  // Keep the latest callback without making the focus-management effect
  // restart whenever a parent creates a new inline callback.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  // On open, move focus into the dialog; Tab/Shift+Tab stay inside it and
  // Escape invokes the same close callback as the close button.
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(4,5,7,0.6)' }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
        className="w-[380px] max-w-[calc(100vw-1rem)] max-h-[calc(100dvh-1rem)] overflow-y-auto bg-surface-raised border border-border-default rounded-lg outline-none flex flex-col"
        style={{ boxShadow: 'var(--shadow-modal)' }}
      >
        <div className="flex items-center justify-between p-5 border-b border-border-subtle">
          <div className="font-sans text-md font-semibold text-text-primary">{title}</div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="focus-ring bg-transparent border-none text-text-muted cursor-pointer text-md"
            >
              &times;
            </button>
          )}
        </div>
        <div className="p-5 font-sans text-sm text-text-secondary">{children}</div>
        {footer && (
          <div className="flex justify-end gap-3 py-4 px-5 border-t border-border-subtle">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
