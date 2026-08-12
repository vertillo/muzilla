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

  // Modal originally had neither a focus trap nor an Escape handler and left
  // these two tests failing on purpose as the target; both behaviors are now
  // these now assert the real (fixed) behavior.
  it('closes on Escape', async () => {
    const onClose = vi.fn()
    render(
      <Modal open title="t" onClose={onClose}>
        c
      </Modal>,
    )
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('moves focus into the dialog on open, onto the first focusable element', async () => {
    render(
      <Modal open title="t" onClose={vi.fn()} footer={<button>OK</button>}>
        c
      </Modal>,
    )
    // The close button (in the header) is the first focusable element
    // in DOM order, so it gets initial focus. Deferred via setTimeout(0)
    // (not synchronous within the mount effect) so a Modal opened from
    // inside a keydown handler doesn't steal the tail (keyup) of that
    // same keystroke — a native <button> fires a click on Enter/Space
    // keyup when focused, which made the close button self-close the
    // modal the instant it opened.
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await vi.waitFor(() => expect(screen.getByLabelText('Close')).toHaveFocus())
  })

  it('focuses the dialog itself when there is no focusable element inside it', async () => {
    render(
      <Modal open title="t">
        c
      </Modal>,
    )
    await vi.waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus())
  })

  it('traps Tab navigation inside the dialog, wrapping from the last focusable element to the first', async () => {
    render(
      <Modal open title="t" onClose={vi.fn()} footer={<button>OK</button>}>
        c
      </Modal>,
    )
    const closeButton = screen.getByLabelText('Close')
    const okButton = screen.getByRole('button', { name: 'OK' })

    await vi.waitFor(() => expect(closeButton).toHaveFocus())
    okButton.focus()
    expect(okButton).toHaveFocus()

    await userEvent.tab()
    expect(closeButton).toHaveFocus()
  })

  it('restores focus to the previously focused element on close', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'open modal'
    document.body.appendChild(trigger)
    trigger.focus()
    expect(trigger).toHaveFocus()

    const { unmount } = render(
      <Modal open title="t" onClose={vi.fn()}>
        c
      </Modal>,
    )
    await vi.waitFor(() => expect(trigger).not.toHaveFocus())

    unmount()
    expect(trigger).toHaveFocus()
    trigger.remove()
  })
})
