import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThreeStateToggle } from '@/components/ui/ThreeStateToggle'

describe('ThreeStateToggle', () => {
  it('defaults to the pending state when no value is given', () => {
    render(<ThreeStateToggle />)
    expect(screen.getByTitle('pending')).toHaveStyle({ color: 'var(--text-muted)' })
  })

  it('calls onChange with "accept" when the accept button is clicked', async () => {
    const onChange = vi.fn()
    render(<ThreeStateToggle value="pending" onChange={onChange} />)
    await userEvent.click(screen.getByTitle('accept'))
    expect(onChange).toHaveBeenCalledWith('accept')
  })

  it('calls onChange with "reject" when the reject button is clicked', async () => {
    const onChange = vi.fn()
    render(<ThreeStateToggle value="pending" onChange={onChange} />)
    await userEvent.click(screen.getByTitle('reject'))
    expect(onChange).toHaveBeenCalledWith('reject')
  })

  it('calls onChange with "pending" when the pending button is clicked from another state', async () => {
    const onChange = vi.fn()
    render(<ThreeStateToggle value="accept" onChange={onChange} />)
    await userEvent.click(screen.getByTitle('pending'))
    expect(onChange).toHaveBeenCalledWith('pending')
  })

  it('disables every button and does not fire onChange when disabled', async () => {
    const onChange = vi.fn()
    render(<ThreeStateToggle value="pending" disabled onChange={onChange} />)
    const acceptButton = screen.getByTitle('accept')
    expect(acceptButton).toBeDisabled()
    await userEvent.click(acceptButton)
    expect(onChange).not.toHaveBeenCalled()
  })
})
