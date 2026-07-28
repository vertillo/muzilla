import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Modal } from '@/components/ui/Modal'

describe('Modal', () => {
  it('renders nothing when open=false', () => {
    render(<Modal open={false} title="hidden">content</Modal>)
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('renders title, children and footer when open', () => {
    render(
      <Modal open title="My Modal" footer={<button>OK</button>}>
        body text
      </Modal>,
    )
    expect(screen.getByText('My Modal')).toBeInTheDocument()
    expect(screen.getByText('body text')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OK' })).toBeInTheDocument()
  })

  it('shows a close button only when onClose is provided, and calls it on click', async () => {
    const onClose = vi.fn()
    const { rerender } = render(<Modal open title="t">c</Modal>)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(
      <Modal open title="t" onClose={onClose}>
        c
      </Modal>,
    )
    await userEvent.click(screen.getByRole('button'))
    expect(onClose).toHaveBeenCalledOnce()
  })

  // docs/PLAN.md §12e step 4.2 / PHASE8_BRIEF.md step 6.5 item 6: "If
  // Modal has no focus trap, write the failing test and fix it in Step
  // 6.5 item 6." It has neither a focus trap nor an Escape handler —
  // confirmed by reading the component (no onKeyDown, no role="dialog",
  // no focus management on mount). These two tests are left failing on
  // purpose as the characterization the fix must satisfy; do not "fix"
  // them by weakening the assertion — fix Modal.tsx instead, in Step 6.5.
  it.fails('closes on Escape (not implemented yet — Step 6.5 item 6)', async () => {
    const onClose = vi.fn()
    render(
      <Modal open title="t" onClose={onClose}>
        c
      </Modal>,
    )
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it.fails('traps focus inside the dialog (not implemented yet — Step 6.5 item 6)', async () => {
    render(
      <Modal open title="t" footer={<button>OK</button>}>
        c
      </Modal>,
    )
    // A real focus trap moves focus into the dialog on open (e.g. onto
    // the first focusable element or the dialog itself) rather than
    // leaving it wherever it was in the page behind the overlay.
    expect(screen.getByRole('dialog')).toHaveFocus()
  })
})
